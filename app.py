"""Monitor de balanças rodoviárias.

Lê o peso dos indicadores de pesagem via TCP e mostra o estado de cada
balança numa tela de acompanhamento (TV) e num painel de cadastro (/admin).
"""
import asyncio
from datetime import datetime
from functools import wraps
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from flask import Flask, Response, jsonify, render_template, request, url_for

app = Flask(__name__)

# Mantém as balanças na ordem do config.json (o Flask ordena por nome)
app.json.sort_keys = False

# Pasta do projeto. Usada para achar o config.json mesmo quando o app é
# iniciado por um serviço/tarefa agendada a partir de outra pasta.
BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_CONFIG = BASE_DIR / "config.json"
ARQUIVO_EXEMPLO = BASE_DIR / "config.example.json"

# Intervalo entre leituras de cada balança (segundos)
INTERVALO_LEITURA = 2.0

# Falhas seguidas antes de marcar OFFLINE. Evita o card piscar quando o
# equipamento recusa uma conexão por estar atendendo outro cliente.
FALHAS_PARA_OFFLINE = 2

# Formas de extrair o peso do texto enviado pelo indicador
PADROES = ("6_digitos", "completo")

# Valores usados quando o config.json não informa a aparência/servidor
APARENCIA_PADRAO = {
    "empresa": "Minha Empresa",
    "subtitulo": "Centro de Operações",
    "titulo": "Monitor de Balanças",
    "logo": "logo.png",
    "cor_primaria": "#2563eb",
    "cor_destaque": "#38bdf8",
    "cor_escura": "#1e293b",
}
SERVIDOR_PADRAO = {
    "host": "0.0.0.0",
    "porta": 8080,
    "senha_admin": "",
}

COR_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Cache em memória com o estado de cada balança, por chave "local | nome"
dados_balancas = {}

# Muda sempre que a configuração é aplicada; a TV usa para se remontar
versao_config = 0

# Loop assíncrono do monitor e as tarefas de leitura em execução
loop_monitor = None
tarefas_monitor = []

# Evita dois salvamentos simultâneos pelo painel
trava_config = threading.Lock()


# ======================================================
# CONFIGURAÇÃO
# ======================================================

def carregar_config() -> dict:
    """Lê o config.json, criando-o a partir do exemplo na primeira execução."""
    if not ARQUIVO_CONFIG.exists() and ARQUIVO_EXEMPLO.exists():
        shutil.copy2(ARQUIVO_EXEMPLO, ARQUIVO_CONFIG)
        print("config.json criado a partir do config.example.json", flush=True)

    bruto = {}
    if ARQUIVO_CONFIG.exists():
        try:
            # utf-8-sig aceita o arquivo salvo pelo Bloco de Notas (com BOM)
            with open(ARQUIVO_CONFIG, encoding="utf-8-sig") as f:
                bruto = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"config.json inválido (linha {e.lineno}, coluna {e.colno}): {e.msg}")

    bruto.setdefault("locais", [])

    locais, erros = validar_locais(bruto)
    servidor = {**SERVIDOR_PADRAO, **(bruto.get("servidor") or {})}
    try:
        servidor["porta"] = int(servidor["porta"])
        if not 1 <= servidor["porta"] <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        erros.append("servidor.porta inválida (use um número de 1 a 65535).")

    if erros:
        raise SystemExit("Erros no config.json:\n- " + "\n- ".join(erros))

    return {
        "aparencia": {**APARENCIA_PADRAO, **(bruto.get("aparencia") or {})},
        "servidor": servidor,
        "locais": locais["locais"],
    }


def salvar_config(config: dict):
    """Grava o config.json, mantendo o anterior em config.json.bak."""
    if ARQUIVO_CONFIG.exists():
        shutil.copy2(ARQUIVO_CONFIG, ARQUIVO_CONFIG.with_name("config.json.bak"))

    # Grava em arquivo temporário e troca, para nunca deixar o config pela metade
    temporario = ARQUIVO_CONFIG.with_name("config.json.tmp")
    temporario.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporario, ARQUIVO_CONFIG)


def chave_balanca(local: str, nome: str) -> str:
    return f"{local} | {nome}"


def listar_balancas(config: dict):
    """Percorre as balanças na ordem da configuração, com os dados do local."""
    for local in config["locais"]:
        for b in local["balancas"]:
            yield chave_balanca(local["nome"], b["nome"]), {
                "local": local["nome"],
                "titulo": local.get("titulo", ""),
                "nome": b["nome"],
                "ip": b["ip"],
                "porta": b["porta"],
                "padrao": b.get("padrao", "6_digitos"),
            }


HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def endereco_valido(endereco: str) -> bool:
    """Aceita IP (v4/v6) ou nome de host (ex.: balanca.empresa.com.br)."""
    try:
        ipaddress.ip_address(endereco)
        return True
    except ValueError:
        pass
    # Só números e pontos, mas não é IP válido (ex.: 192.168.1.300)
    if re.fullmatch(r"[\d.]+", endereco):
        return False
    return bool(HOSTNAME.match(endereco))


def validar_locais(dados) -> tuple:
    """Valida e limpa a lista de locais e balanças.

    Retorna ({"locais": [...]}, lista_de_erros).
    """
    if not isinstance(dados, dict) or not isinstance(dados.get("locais"), list):
        return None, ["Formato de configuração inválido."]

    erros = []
    locais = []
    locais_vistos = set()

    for i, local in enumerate(dados["locais"], 1):
        if not isinstance(local, dict):
            erros.append(f"Local {i}: formato inválido.")
            continue

        nome_local = str(local.get("nome", "")).strip()
        titulo = str(local.get("titulo", "")).strip()
        ref_local = nome_local or f"Local {i}"

        if not nome_local:
            erros.append(f"Local {i}: informe o nome do local.")
        elif nome_local.lower() in locais_vistos:
            erros.append(f"O local \"{nome_local}\" está cadastrado mais de uma vez.")
        locais_vistos.add(nome_local.lower())

        balancas = []
        nomes_vistos = set()
        for j, b in enumerate(local.get("balancas") or [], 1):
            if not isinstance(b, dict):
                erros.append(f"{ref_local}, balança {j}: formato inválido.")
                continue

            nome = str(b.get("nome", "")).strip()
            ip = str(b.get("ip", "")).strip()
            padrao = b.get("padrao") if b.get("padrao") in PADROES else "6_digitos"
            ref = f"{ref_local}, balança {nome or j}"

            if not nome:
                erros.append(f"{ref_local}, balança {j}: informe o nome da balança.")
            elif nome.lower() in nomes_vistos:
                erros.append(f"{ref_local}: a balança \"{nome}\" está repetida.")
            nomes_vistos.add(nome.lower())

            if not endereco_valido(ip):
                erros.append(f"{ref}: IP inválido (\"{ip}\").")

            try:
                porta = int(b.get("porta"))
                if not 1 <= porta <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                erros.append(f"{ref}: porta inválida (use um número de 1 a 65535).")
                porta = None

            balancas.append({"nome": nome, "ip": ip, "porta": porta, "padrao": padrao})

        locais.append({"nome": nome_local, "titulo": titulo, "balancas": balancas})

    return {"locais": locais}, erros


# ======================================================
# APARÊNCIA (nome da empresa, logo e cores)
# ======================================================

def misturar(cor: str, outra: str, proporcao: float) -> str:
    """Mistura duas cores hex; "proporcao" é o peso da primeira (0 a 1)."""
    a = [int(cor[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(outra[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * proporcao + y * (1 - proporcao)):02x}" for x, y in zip(a, b))


def cores_marca(aparencia: dict) -> dict:
    """Variáveis CSS da marca. Cores fora do formato #RRGGBB usam o padrão."""
    def cor(campo):
        valor = str(aparencia.get(campo, ""))
        return valor if COR_HEX.match(valor) else APARENCIA_PADRAO[campo]

    primaria = cor("cor_primaria")
    return {
        "marca-primaria": primaria,
        "marca-primaria-escura": misturar(primaria, "#000000", 0.75),
        "marca-primaria-suave": misturar(primaria, "#ffffff", 0.15),
        "marca-destaque": cor("cor_destaque"),
        "marca-escura": cor("cor_escura"),
    }


def url_logo(logo: str):
    """URL do logo: endereço http(s) ou arquivo dentro de static/. None se não houver."""
    logo = str(logo or "").strip()
    if re.match(r"^https?://", logo):
        return logo
    if logo and (BASE_DIR / "static" / logo).is_file():
        return url_for("static", filename=logo)
    return None


@app.context_processor
def dados_aparencia():
    aparencia = configuracao["aparencia"]
    return {
        "aparencia": aparencia,
        "cores_marca": cores_marca(aparencia),
        "logo_url": url_logo(aparencia.get("logo")),
    }


# ======================================================
# LEITURA DAS BALANÇAS
# ======================================================

def extrair_peso(texto_bruto: str, padrao: str = "6_digitos") -> str:
    if not texto_bruto:
        return "---"

    # Remove caracteres invisíveis de controle ASCII (STX, ETX, etc.)
    texto_limpo = re.sub(r"[^\x20-\x7E]", "", texto_bruto)
    sequencias = re.findall(r"\d+", texto_limpo)

    if not sequencias:
        return "Sem Leitura"

    # Captura a maior sequência numérica (geralmente o peso bruto)
    bloco_principal = max(sequencias, key=len)

    if padrao == "6_digitos":
        bloco_peso = (
            bloco_principal[:6]
            if len(bloco_principal) >= 6
            else bloco_principal
        )
    else:
        bloco_peso = bloco_principal

    try:
        peso_int = int(bloco_peso)
        return f"{peso_int:,} kg".replace(",", ".")
    except ValueError:
        return "Erro Leitura"


async def ler_texto(ip: str, porta: int) -> str:
    """Conecta, lê um pacote e desconecta, devolvendo o texto bruto.

    Não mantém a conexão aberta: muitos conversores serial/Ethernet aceitam
    um único cliente TCP, e a conexão presa bloquearia o sistema de pesagem
    (ERP) que também consulta a balança. Abrir uma conexão nova a cada
    leitura também garante que o peso lido é o atual, e não um dado antigo
    acumulado no buffer do socket.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, porta), timeout=3.0
    )
    try:
        dados = await asyncio.wait_for(reader.read(1024), timeout=5.0)
        if not dados:
            raise ConnectionError("Conexão encerrada sem enviar dados")

        return dados.decode("latin1", errors="ignore")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def monitorar_balanca(chave: str, balanca: dict):
    """Monitora a balança de forma assíncrona via Socket TCP."""
    falhas_seguidas = 0

    while True:
        try:
            texto = await ler_texto(balanca["ip"], balanca["porta"])
            peso_formatado = extrair_peso(texto, balanca["padrao"])
            falhas_seguidas = 0
            agora = time.time()

            dados_balancas[chave] = {
                **dados_balancas[chave],
                "status": "ONLINE",
                "peso": peso_formatado,
                "hora": datetime.now().strftime("%H:%M:%S"),
                "ultima_leitura": agora,
                "ultima_verificacao": agora,
            }

        except Exception:
            falhas_seguidas += 1
            anterior = dados_balancas[chave]

            if falhas_seguidas >= FALHAS_PARA_OFFLINE:
                # Mantém "hora" e "ultima_leitura" da última leitura válida
                dados_balancas[chave] = {
                    **anterior,
                    "status": "OFFLINE",
                    "peso": "---",
                    "ultima_verificacao": time.time(),
                }
            else:
                dados_balancas[chave] = {
                    **anterior,
                    "ultima_verificacao": time.time(),
                }

        await asyncio.sleep(INTERVALO_LEITURA)


async def aplicar_configuracao(config: dict):
    """(Re)inicia o monitoramento com a configuração informada.

    Roda dentro do loop do monitor. Balanças que não mudaram de IP/porta
    mantêm o último estado, para a TV não piscar ao salvar o painel.
    """
    global dados_balancas, tarefas_monitor, versao_config

    for tarefa in tarefas_monitor:
        tarefa.cancel()
    await asyncio.gather(*tarefas_monitor, return_exceptions=True)

    novos = {}
    balancas = list(listar_balancas(config))
    for chave, b in balancas:
        estatico = {
            "local": b["local"],
            "titulo": b["titulo"],
            "nome": b["nome"],
            "ip": b["ip"],
            "porta": b["porta"],
        }
        anterior = dados_balancas.get(chave)
        if anterior and anterior["ip"] == b["ip"] and anterior["porta"] == b["porta"]:
            novos[chave] = {**anterior, **estatico}
        else:
            novos[chave] = {
                **estatico,
                "status": "OFFLINE",
                "peso": "---",
                "hora": "-",
                "ultima_leitura": None,
                "ultima_verificacao": None,
            }

    dados_balancas = novos
    versao_config += 1
    tarefas_monitor = [
        asyncio.ensure_future(monitorar_balanca(chave, b)) for chave, b in balancas
    ]


def executar_no_monitor(corrotina, timeout: float):
    """Executa uma corrotina no loop do monitor a partir de uma rota do Flask."""
    if loop_monitor is None:
        raise RuntimeError("Monitor ainda não iniciado")
    return asyncio.run_coroutine_threadsafe(corrotina, loop_monitor).result(timeout)


def executar_async_in_background():
    """Roda o loop assíncrono em uma thread dedicada."""
    global loop_monitor
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(aplicar_configuracao(configuracao))
    loop_monitor = loop
    loop.run_forever()


# Carrega a configuração das balanças
configuracao = carregar_config()

# Inicia o motor de leitura assíncrona em segundo plano
thread_monitor = threading.Thread(
    target=executar_async_in_background, daemon=True
)
thread_monitor.start()


# ======================================================
# ROTAS DA TV
# ======================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/balancas")
def api_balancas():
    """Endpoint REST JSON consumido pelo frontend em tempo real.

    Timestamps em epoch (segundos). "servidor_hora" permite ao frontend
    calcular a idade de cada verificação sem depender do relógio da TV.
    """
    return jsonify({
        "servidor_hora": time.time(),
        "versao_config": versao_config,
        "balancas": dados_balancas,
    })


# ======================================================
# PAINEL DE CONFIGURAÇÃO (/admin)
# ======================================================

def exigir_senha(rota):
    """Protege a rota com senha (HTTP Basic) quando "senha_admin" está definida.

    O navegador pede usuário e senha; o usuário pode ser qualquer um.
    """
    @wraps(rota)
    def protegida(*args, **kwargs):
        senha = str(configuracao["servidor"].get("senha_admin") or "")
        if senha:
            auth = request.authorization
            enviada = (auth.password or "") if auth else ""
            if not hmac.compare_digest(enviada.encode(), senha.encode()):
                return Response(
                    "Senha necessária para acessar o painel.", 401,
                    {"WWW-Authenticate": 'Basic realm="Monitor de Balancas"'},
                )
        return rota(*args, **kwargs)
    return protegida


@app.route("/admin")
@exigir_senha
def admin():
    return render_template("admin.html")


@app.route("/api/admin/config", methods=["GET"])
@exigir_senha
def admin_ler_config():
    return jsonify({"versao": versao_config, "locais": configuracao["locais"]})


@app.route("/api/admin/config", methods=["POST"])
@exigir_senha
def admin_salvar_config():
    """Valida, grava o config.json e aplica sem reiniciar o monitor.

    O painel envia a "versao" que carregou; se outra pessoa salvou nesse
    meio tempo, recusa para não sobrescrever a alteração dela.
    """
    global configuracao

    dados = request.get_json(silent=True)
    locais, erros = validar_locais(dados)
    if erros:
        return jsonify({"ok": False, "erros": erros}), 400

    with trava_config:
        if dados.get("versao") != versao_config:
            return jsonify({"ok": False, "erros": [
                "A configuração foi alterada em outro computador. "
                "A página será recarregada; refaça a alteração."
            ], "conflito": True}), 409

        # O painel só altera os locais; aparência e servidor são mantidos
        nova = {**configuracao, **locais}
        salvar_config(nova)
        configuracao = nova
        executar_no_monitor(aplicar_configuracao(nova), timeout=15)

    return jsonify({"ok": True, "versao": versao_config})


@app.route("/api/admin/testar", methods=["POST"])
@exigir_senha
def admin_testar_conexao():
    """Faz uma leitura avulsa de uma balança para conferir IP, porta e peso."""
    dados = request.get_json(silent=True) or {}
    ip = str(dados.get("ip", "")).strip()
    padrao = dados.get("padrao") if dados.get("padrao") in PADROES else "6_digitos"

    try:
        porta = int(dados.get("porta"))
        if not 1 <= porta <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "erro": "Porta inválida"})
    if not endereco_valido(ip):
        return jsonify({"ok": False, "erro": "IP inválido"})

    inicio = time.perf_counter()
    try:
        texto = executar_no_monitor(ler_texto(ip, porta), timeout=10)
    except asyncio.TimeoutError:
        erro = "Tempo esgotado (sem resposta)"
    except ConnectionRefusedError:
        erro = "Conexão recusada (porta fechada ou equipamento ocupado)"
    except ConnectionError as e:
        erro = str(e)
    except OSError as e:
        erro = f"Erro de rede: {e.strerror or e}"
    except Exception as e:
        erro = f"Erro: {e}"
    else:
        return jsonify({
            "ok": True,
            "peso": extrair_peso(texto, padrao),
            "tempo_ms": round((time.perf_counter() - inicio) * 1000),
            # Amostra do que o indicador enviou, para conferir o formato
            "bruto": re.sub(r"[^\x20-\x7E]", "·", texto)[:80],
        })

    return jsonify({"ok": False, "erro": erro})


if __name__ == "__main__":
    host = str(configuracao["servidor"]["host"])
    porta = configuracao["servidor"]["porta"]

    # waitress é o servidor recomendado para produção
    # (pip install -r requirements.txt). Sem ele, usa o servidor do Flask.
    try:
        from waitress import serve
    except ImportError:
        print("AVISO: waitress não instalado, usando servidor de desenvolvimento do Flask")
        app.run(host=host, port=porta, debug=False)
    else:
        print(f"Monitor de balanças rodando em http://{host}:{porta} (waitress)", flush=True)
        serve(app, host=host, port=porta)

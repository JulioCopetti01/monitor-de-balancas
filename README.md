# Monitor de Balanças

Painel web para acompanhar, em tempo real, se as **balanças rodoviárias** estão respondendo na rede e qual peso estão marcando. Feito para ficar aberto numa **TV de NOC / centro de operações**, com um painel de cadastro para gerenciar locais, IPs e portas sem mexer em código.

Funciona com indicadores de pesagem que enviam o peso por **TCP/IP**: indicadores com saída Ethernet ou indicadores seriais ligados a um **conversor serial/Ethernet** (como os usados para o ERP consultar a balança remotamente).

## Por que existe

Em muitas empresas o sistema de gestão (ERP, muitas vezes em nuvem) consulta o peso direto no IP da balança, pelo link principal ou de backup de cada unidade. Quando um link cai ou o conversor trava, o problema só aparece quando o motorista já está em cima da balança.

Este monitor testa cada balança continuamente e mostra numa tela só:

- quais estão **ONLINE**, **OFFLINE** ou **ATRASADO**;
- o **peso atual** de cada uma;
- a situação de cada **local** (por exemplo, "2/3 online").

## Funcionalidades

**Tela da TV (`/`)**
- Uma linha por local, com as balanças lado a lado (por exemplo: rede interna, link principal, link backup).
- IP e porta em cada card, peso atual e hora da última leitura.
- Tudo cabe na tela, sem scroll: colunas e fontes se ajustam ao tamanho da TV.
- Resumo de online, offline e atrasados, além de relógio no cabeçalho.
- Faixa vermelha **"MONITOR SEM COMUNICAÇÃO"** se a TV perder contato com o servidor, para nunca mostrar dados velhos como se fossem atuais.

**Painel de cadastro (`/admin`)**
- Cadastro de locais e balanças por modal: nome, IP, porta e formato de leitura.
- Botão **Testar conexão**, que lê a balança na hora e mostra o peso e o texto bruto recebido.
- Reordenar, editar e excluir. Ao salvar, o monitor aplica na hora, sem reiniciar.
- Status ao vivo de cada balança.
- Senha opcional.

**Personalização**
- Nome da empresa, títulos, logo e cores definidos no `config.json`.

## Como a leitura funciona

A cada 2 segundos, para cada balança, o monitor:

1. abre uma conexão TCP com o IP e a porta cadastrados (limite de 3 s);
2. lê o que o indicador enviar (limite de 5 s);
3. **fecha a conexão na hora**;
4. extrai o peso do texto recebido.

Fechar a conexão logo após ler é proposital: muitos conversores serial/Ethernet aceitam **só um cliente por vez**. Se o monitor ficasse conectado, poderia bloquear o próprio sistema de pesagem.

Uma balança só é marcada OFFLINE depois de **2 falhas seguidas**, para tolerar uma colisão momentânea com outro sistema conectado.

### Formato do peso

O texto recebido passa por estes passos:
- os caracteres de controle (STX, ETX, CR...) são removidos;
- o monitor pega a **maior sequência de números**;
- conforme o campo `padrao` da balança:

| `padrao` | Como interpreta | Exemplo recebido | Resultado |
|---|---|---|---|
| `6_digitos` (padrão) | 6 primeiros dígitos da sequência | `␂q0 012340000000␍` | `12.340 kg` |
| `completo` | a sequência inteira | `␂ 12340␍` | `12.340 kg` |

Use o **Testar conexão** do painel para ver o texto bruto do seu indicador e escolher o formato certo.

## Início rápido

Requisitos: **Python 3.9 ou mais novo**.

> No Windows, se o comando `python` abrir a Microsoft Store ou não for encontrado, use `py` no lugar (ex.: `py app.py`, `py -m pip install -r requirements.txt`).

```bash
git clone https://github.com/JulioCopetti01/monitor-de-balancas.git
cd monitor-de-balancas
pip install -r requirements.txt
```

Para testar sem uma balança de verdade, abra **dois terminais**:

```bash
# Terminal 1: duas balanças simuladas nas portas 9000 e 9001
python simulador_balanca.py
```

```bash
# Terminal 2: o monitor
python app.py
```

Abra no navegador:
- TV: http://localhost:8080
- Painel: http://localhost:8080/admin

Na primeira execução, o `config.json` é criado a partir do `config.example.json`. O exemplo já tem as duas balanças do simulador (que aparecem **ONLINE**) e três balanças de exemplo com IPs fictícios (que aparecem **OFFLINE**). Depois é só cadastrar as suas pelo painel e apagar os exemplos.

## Configuração (`config.json`)

O painel `/admin` edita a lista de locais e balanças. Aparência e servidor são editados direto no arquivo; reinicie o monitor depois de alterar essas duas seções.

```json
{
  "aparencia": {
    "empresa": "Minha Empresa",
    "subtitulo": "Centro de Operações",
    "titulo": "Monitor de Balanças",
    "logo": "logo.png",
    "cor_primaria": "#2563eb",
    "cor_destaque": "#38bdf8",
    "cor_escura": "#1e293b"
  },
  "servidor": {
    "host": "0.0.0.0",
    "porta": 8080,
    "senha_admin": ""
  },
  "locais": [
    {
      "nome": "Unidade Exemplo",
      "titulo": "Região Norte",
      "balancas": [
        { "nome": "Link principal", "ip": "192.168.1.51", "porta": 9000, "padrao": "6_digitos" }
      ]
    }
  ]
}
```

| Campo | Descrição |
|---|---|
| `aparencia.empresa` | Nome exibido no título da página (e no lugar do logo, se não houver logo). |
| `aparencia.subtitulo` / `titulo` | Textos do cabeçalho. |
| `aparencia.logo` | Arquivo dentro de `static/` (ex.: `logo.png`) ou uma URL `https://...`. |
| `aparencia.cor_primaria` | Botões e destaques do painel (formato `#RRGGBB`). |
| `aparencia.cor_destaque` | Faixa do cabeçalho e título dos locais. |
| `aparencia.cor_escura` | Fundo do rótulo de cada local na TV. |
| `servidor.host` | `0.0.0.0` aceita acesso da rede; `127.0.0.1`, só da própria máquina. |
| `servidor.porta` | Porta do site (padrão `8080`). |
| `servidor.senha_admin` | Se preenchida, o `/admin` pede senha (qualquer usuário + essa senha). |
| `locais[].nome` | Nome do local (fazenda, filial, pátio...). |
| `locais[].titulo` | Texto opcional exibido acima do nome do local. |
| `balancas[].nome` | Nome da balança dentro do local (ex.: "Interno", "Link backup"). |
| `balancas[].ip` / `porta` | Endereço TCP do indicador ou do conversor. Aceita IP ou nome de host. |
| `balancas[].padrao` | `6_digitos` ou `completo` (veja [Formato do peso](#formato-do-peso)). |

As cores de status (verde, vermelho e âmbar) são fixas, para o alerta ficar claro qualquer que seja a cor da marca.

## Rodando como serviço no Windows

O script `servico_monitor.ps1` cria uma **tarefa agendada** que:
- sobe junto com o Windows, sem precisar de usuário logado;
- reinicia o monitor se ele cair;
- grava o log em `logs\monitor.log`.

No PowerShell **como Administrador**, dentro da pasta do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File .\servico_monitor.ps1 instalar
```

Outros comandos: `status`, `reiniciar` (depois de alterar o `config.json` ou o código), `parar`, `iniciar` e `remover`.

## Rodando como serviço no Linux (systemd)

```ini
# /etc/systemd/system/monitor-de-balancas.service
[Unit]
Description=Monitor de Balancas
After=network-online.target

[Service]
WorkingDirectory=/opt/monitor-de-balancas
ExecStart=/usr/bin/python3 /opt/monitor-de-balancas/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now monitor-de-balancas
```

## Deixando a TV no ar

Abra o navegador da TV em tela cheia, com `F11` ou em modo quiosque:

```bash
chrome --kiosk http://IP-DO-SERVIDOR:8080
```

## API

`GET /api/balancas` devolve o estado de todas as balanças. Dá para integrar com Zabbix, Grafana ou scripts próprios.

```json
{
  "servidor_hora": 1793000000.12,
  "versao_config": 3,
  "balancas": {
    "Unidade Exemplo | Link principal": {
      "local": "Unidade Exemplo",
      "titulo": "Região Norte",
      "nome": "Link principal",
      "ip": "192.168.1.51",
      "porta": 9000,
      "status": "ONLINE",
      "peso": "12.340 kg",
      "hora": "14:05:31",
      "ultima_leitura": 1793000000.05,
      "ultima_verificacao": 1793000000.05
    }
  }
}
```

`servidor_hora`, `ultima_leitura` e `ultima_verificacao` são timestamps Unix (segundos); `hora` é a hora local da última leitura válida. `versao_config` muda sempre que o cadastro é salvo.

## Ajustes finos

No início do `app.py`:

| Constante | Padrão | Para que serve |
|---|---|---|
| `INTERVALO_LEITURA` | `2.0` | Segundos entre leituras de cada balança. Aumente para consultar os equipamentos com menos frequência. |
| `FALHAS_PARA_OFFLINE` | `2` | Falhas seguidas antes de marcar OFFLINE. Aumente se a sua rede oscila. |

## Solução de problemas

**A balança aparece OFFLINE, mas o equipamento está ligado.**
- Confira IP e porta com o **Testar conexão** do painel.
- Se o conversor aceita um cliente só e outro sistema fica conectado **o tempo todo**, o monitor não consegue entrar. Veja no conversor quantas conexões simultâneas ele aceita.
- Firewall ou redirecionamento de porta (NAT) do link podem bloquear o acesso a partir do servidor do monitor.

**Aparece "Sem Leitura" ou um peso estranho.**
- O indicador respondeu, mas num formato diferente do esperado. Use o **Testar conexão**, veja o texto bruto e troque o `padrao` da balança.

**"A porta 8080 já está em uso".**
- Troque `servidor.porta` no `config.json` ou feche o programa que está usando a porta.

**A TV mostra "MONITOR SEM COMUNICAÇÃO".**
- O servidor do monitor parou ou a TV perdeu a rede. No Windows, rode `servico_monitor.ps1 status` e veja o `logs\monitor.log`.

## Segurança

- Por padrão o painel `/admin` **não tem senha**: qualquer pessoa que acessar o site consegue alterar IPs e portas. Defina `servidor.senha_admin` se a rede não for restrita.
- A senha usa HTTP Basic, sem criptografia própria. **Não exponha o monitor diretamente na internet**: use-o na rede interna ou atrás de VPN ou proxy com HTTPS.

## Estrutura

```
app.py                  servidor web + leitura das balanças
simulador_balanca.py    balanças simuladas para testes
config.example.json     configuração de exemplo (copiada para config.json)
templates/index.html    tela da TV
templates/admin.html    painel de cadastro
static/style.css        estilos da TV (e base do painel)
static/admin.css        estilos do painel
servico_monitor.ps1     instala/gerencia como tarefa agendada no Windows
iniciar_monitor.bat     mantém o monitor rodando e grava o log
```

## Contribuindo

Sugestões e correções são bem-vindas: abra uma *issue* ou um *pull request*. Se você usa um indicador com formato de peso diferente, compartilhar um exemplo do texto bruto ajuda a suportar mais modelos.

## Licença

[MIT](LICENSE)

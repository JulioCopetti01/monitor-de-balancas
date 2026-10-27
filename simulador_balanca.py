"""Simulador de indicador de pesagem via TCP.

Abre uma ou mais portas e, a cada conexão, envia o peso continuamente
(10 vezes por segundo), como um conversor serial/Ethernet ligado a um
indicador. Serve para testar o monitor sem uma balança de verdade.

Cada pacote tem o formato:  STX + "q0 " + peso (6 dígitos) + tara (6 dígitos) + CR
que é lido pelo monitor com o padrão "6_digitos".

O peso simula caminhões entrando e saindo: balança vazia, subida,
peso estável e descida, num ciclo de cerca de 40 segundos.

Uso:
    python simulador_balanca.py                      # portas 9000 e 9001
    python simulador_balanca.py --portas 9000 9001 9002
    python simulador_balanca.py --host 0.0.0.0       # aceita conexões da rede

Para ver uma balança ficar OFFLINE no monitor, pare o simulador (CTRL+C).
"""
import argparse
import asyncio
import random
import time

STX = "\x02"
CR = "\r"

# Duração de cada fase do ciclo de pesagem (segundos)
VAZIA, SUBINDO, ESTAVEL, DESCENDO = 10, 5, 20, 5
CICLO = VAZIA + SUBINDO + ESTAVEL + DESCENDO


def peso_atual(porta: int) -> int:
    """Peso simulado no momento, diferente para cada porta e cada ciclo."""
    agora = time.time() + porta * 7  # defasa as balanças entre si
    numero_ciclo, posicao = divmod(agora, CICLO)
    carga = random.Random(f"{porta}-{int(numero_ciclo)}").randrange(12000, 48000, 10)

    if posicao < VAZIA:
        return 0
    posicao -= VAZIA
    if posicao < SUBINDO:
        return round(carga * posicao / SUBINDO / 10) * 10
    posicao -= SUBINDO
    if posicao < ESTAVEL:
        return carga
    posicao -= ESTAVEL
    return round(carga * (1 - posicao / DESCENDO) / 10) * 10


def pacote(peso: int) -> bytes:
    return f"{STX}q0 {peso:06d}{0:06d}{CR}".encode("ascii")


async def atender(porta: int, reader, writer):
    cliente = writer.get_extra_info("peername")
    print(f"[{porta}] conexão de {cliente[0]}:{cliente[1]}", flush=True)
    try:
        while True:
            writer.write(pacote(peso_atual(porta)))
            await writer.drain()
            await asyncio.sleep(0.1)
    except (ConnectionError, OSError):
        pass  # o monitor desconecta logo após ler, isso é o esperado
    finally:
        writer.close()


async def main(host: str, portas: list):
    servidores = []
    for porta in portas:
        servidor = await asyncio.start_server(
            lambda r, w, p=porta: atender(p, r, w), host, porta
        )
        servidores.append(servidor)
        print(f"Balança simulada em {host}:{porta}", flush=True)

    print("Pressione CTRL+C para parar.", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servidores))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulador de indicador de pesagem via TCP")
    parser.add_argument("--host", default="127.0.0.1", help="endereço para escutar (padrão: 127.0.0.1)")
    parser.add_argument("--portas", type=int, nargs="+", default=[9000, 9001],
                        help="portas das balanças simuladas (padrão: 9000 9001)")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.portas))
    except KeyboardInterrupt:
        print("\nSimulador encerrado.")

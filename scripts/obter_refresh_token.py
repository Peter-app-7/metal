#!/usr/bin/env python3
"""Corre uma vez no Mac para ligar o Spotify: python3 scripts/obter_refresh_token.py

Abre o browser para autorizares a app, obtém o refresh token e guarda as três
chaves como GitHub Secrets com o comando gh. Se o gh não estiver disponível, mostra-as
no ecrã para as colares à mão. Só precisa de acesso de leitura aos artistas seguidos.
"""
import base64
import getpass
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REPOSITORIO = "Peter-app-7/metal"
PORTA = 8888
# O Spotify não aceita "localhost": exige o endereço 127.0.0.1.
REDIRECT_URI = "http://127.0.0.1:%d/callback" % PORTA
SCOPE = "user-follow-read"
URL_AUTORIZAR = "https://accounts.spotify.com/authorize"
URL_TOKEN = "https://accounts.spotify.com/api/token"
URL_SEGUIDOS = "https://api.spotify.com/v1/me/following?type=artist&limit=1"


def esperar_codigo(estado_esperado):
    """Recebe o regresso do Spotify. Só escuta neste Mac e só aceita o 'state' certo."""
    resultado = {}

    class Tratador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            partes = urllib.parse.urlsplit(self.path)
            if partes.path != "/callback":
                self.send_error(404)
                return
            parametros = urllib.parse.parse_qs(partes.query)
            if parametros.get("state", [""])[0] != estado_esperado:
                resultado["erro"] = "resposta com 'state' errado"
            elif "error" in parametros:
                resultado["erro"] = parametros["error"][0]
            else:
                resultado["codigo"] = parametros.get("code", [""])[0]
            texto = "Pronto. Podes fechar esta janela e voltar ao Terminal."
            if "erro" in resultado:
                texto = "Não correu bem. Volta ao Terminal."
            corpo = ("<!doctype html><meta charset=utf-8><p>%s</p>" % texto).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *argumentos):
            pass  # o endereço traz o código de autorização: não o mostrar

    class Servidor(http.server.HTTPServer):
        timeout = 300  # segundos à espera de cada pedido

        def handle_timeout(self):
            resultado["erro"] = "passaram 5 minutos sem resposta"

    servidor = Servidor(("127.0.0.1", PORTA), Tratador)
    try:
        # Repete porque o browser pode pedir outras coisas antes, como o favicon.
        while not resultado:
            servidor.handle_request()
    finally:
        servidor.server_close()
    if "erro" in resultado:
        raise SystemExit("Autorização falhou: %s" % resultado["erro"])
    if not resultado.get("codigo"):
        raise SystemExit("O Spotify não devolveu um código de autorização.")
    return resultado["codigo"]


def trocar_codigo(client_id, client_secret, codigo):
    credenciais = base64.b64encode(("%s:%s" % (client_id, client_secret)).encode()).decode()
    pedido = urllib.request.Request(
        URL_TOKEN,
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": REDIRECT_URI,
        }).encode(),
        headers={"Authorization": "Basic " + credenciais,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        raise SystemExit("O Spotify recusou o pedido (HTTP %d). Confirma o Client ID, o Client Secret "
                         "e o Redirect URI %s." % (erro.code, REDIRECT_URI))


def confirmar_acesso(token):
    """Lê um artista seguido, para confirmar logo que a ligação funciona."""
    pedido = urllib.request.Request(URL_SEGUIDOS, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            total = json.loads(resposta.read().decode("utf-8"))["artists"].get("total")
        print("Ligação confirmada: segues %s artistas no Spotify." % total)
    except urllib.error.HTTPError as erro:
        print("Atenção: o teste de leitura devolveu HTTP %d. As apps em modo de "
              "desenvolvimento exigem Spotify Premium na conta dona da app." % erro.code)


def encontrar_gh():
    return shutil.which("gh") or next(
        (c for c in [os.path.expanduser("~/.local/bin/gh")] if os.path.exists(c)), None)


def guardar_segredos(segredos):
    gh = encontrar_gh()
    if gh and subprocess.run([gh, "auth", "status"], capture_output=True).returncode == 0:
        for nome, valor in segredos.items():
            # O valor segue pela entrada padrão, para não aparecer na lista de processos.
            subprocess.run([gh, "secret", "set", nome, "--repo", REPOSITORIO],
                           input=valor.encode(), check=True, capture_output=True)
            print("Secret guardado: %s" % nome)
        return True
    print("\nO comando gh não está disponível. Em github.com/%s/settings/secrets/actions," % REPOSITORIO)
    print("cria estes três secrets (e não partilhes estes valores):\n")
    for nome, valor in segredos.items():
        print("%s = %s" % (nome, valor))
    return False


def main():
    print("Redirect URI a registar na app do Spotify: %s\n" % REDIRECT_URI)
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret (não aparece enquanto escreves): ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Faltam o Client ID ou o Client Secret.")

    estado = secrets.token_urlsafe(24)
    endereco = URL_AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": estado,
    })
    print("\nA abrir o browser. Se não abrir, copia este endereço:\n%s\n" % endereco)
    webbrowser.open(endereco)

    codigo = esperar_codigo(estado)
    tokens = trocar_codigo(client_id, client_secret, codigo)
    if "refresh_token" not in tokens:
        raise SystemExit("O Spotify não devolveu um refresh token.")
    confirmar_acesso(tokens["access_token"])
    guardar_segredos({
        "SPOTIFY_CLIENT_ID": client_id,
        "SPOTIFY_CLIENT_SECRET": client_secret,
        "SPOTIFY_REFRESH_TOKEN": tokens["refresh_token"],
    })
    print("\nConcluído.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nCancelado.")

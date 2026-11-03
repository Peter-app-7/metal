#!/usr/bin/env python3
"""Vai buscar os dados às fontes e gera docs/data.json. Corre na GitHub Action.

Só usa a biblioteca padrão do Python, por isso não é preciso instalar nada.
As chaves vêm de variáveis de ambiente (GitHub Secrets) e nunca são escritas nos dados.
Sem chaves, corre na mesma: usa bands.txt e deixa os concertos por ativar.
"""
import base64
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FICHEIRO_DADOS = RAIZ / "docs" / "data.json"
FICHEIRO_CACHE = RAIZ / "cache" / "musicbrainz.json"
FICHEIRO_BANDAS = RAIZ / "bands.txt"
FICHEIRO_CATEGORIAS = RAIZ / "categorias.json"
FICHEIRO_FEEDS = RAIZ / "feeds.txt"

USER_AGENT = "metal-app/1.0 (+https://github.com/Peter-app-7/metal)"
DIAS_RECENTES = 90
DIAS_CACHE = 30
MAX_LANCAMENTOS_POR_BANDA = 10
MAX_NOTICIAS_POR_FEED = 30
MAX_NOTICIAS = 150
DIAS_NOTICIAS = 30
# Uma categoria só vale se tiver pelo menos esta fração dos votos da etiqueta mais votada.
# Evita, por exemplo, pôr em Doom/Sludge uma banda de grunge com um voto em "sludge".
FRACAO_MINIMA_CATEGORIA = 0.2
MAX_TAMANHO_FEED = 5 * 1024 * 1024

OUTROS = "Outros"
IGNORAR = "Ignorar"
CATEGORIAS = ["Thrash", "Death", "Black", "Doom/Sludge", "Nu/Metalcore"]

# Para cada etiqueta, ganha a primeira regra que encaixa. A ordem importa:
# "death doom" tem de vir antes de "doom" e "deathcore" não é "death metal".
REGRAS_CATEGORIA = [
    (r"death doom", "Doom/Sludge"),
    (r"deathcore", "Nu/Metalcore"),
    (r"blackened death", "Death"),
    (r"blackened thrash|black thrash", "Thrash"),
    (r"thrash|speed metal|groove metal", "Thrash"),
    (r"melodic death|melodeath|death metal|deathgrind|goregrind|grindcore", "Death"),
    (r"black metal|blackened|blackgaze|dsbm", "Black"),
    (r"doom|sludge|stoner|drone metal|post metal", "Doom/Sludge"),
    (r"nu metal|metalcore|mathcore|post hardcore|rap metal", "Nu/Metalcore"),
]

# Géneros que indicam que o artista não é música.
GENEROS_NAO_MUSICA = re.compile(
    r"audiobook|audiolivro|audio drama|podcast|spoken word|stand up|standup|"
    r"poetry|sermon|narrat|lecture"
)

# No MusicBrainz, só contam álbuns e EPs de estúdio. Os ao vivo ficam de fora porque
# muitas bandas têm dezenas de gravações de concertos, que tapariam os álbuns a sério.
# Para os voltar a mostrar, basta tirar "Live" desta lista.
TIPOS_SECUNDARIOS_EXCLUIDOS = {
    "Compilation", "Live", "Remix", "DJ-mix", "Mixtape/Street", "Demo", "Interview",
    "Audiobook", "Audio drama", "Spoken word", "Soundtrack",
}
TIPOS_NAO_MUSICA = {"Audiobook", "Audio drama", "Spoken word", "Interview"}


class ErroFonte(Exception):
    """Falha de uma fonte. A mensagem vai para os avisos, por isso nunca leva endereços nem chaves."""


def agora():
    return datetime.now(timezone.utc)


def normalizar(texto):
    """Minúsculas, sem acentos nem pontuação: 'Mötley Crüe' passa a 'motley crue'."""
    sem_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"[\W_]+", " ", sem_acentos.lower()).strip()


def chave_banda(nome):
    return normalizar(nome) or nome.strip().lower()


def pedir(url, cabecalhos=None, corpo=None, tentativas=3, espera_maxima=120):
    """Faz um pedido HTTP. Devolve (estado, bytes). Repete em erros temporários."""
    host = urllib.parse.urlsplit(url).netloc
    base = {"User-Agent": USER_AGENT}
    base.update(cabecalhos or {})
    ultimo = "sem resposta"
    for tentativa in range(tentativas):
        espera = 2 ** tentativa
        try:
            pedido = urllib.request.Request(url, data=corpo, headers=base)
            with urllib.request.urlopen(pedido, timeout=30) as resposta:
                return resposta.status, resposta.read(MAX_TAMANHO_FEED + 1)
        except urllib.error.HTTPError as erro:
            if erro.code != 429 and erro.code < 500:
                return erro.code, erro.read(4096)
            ultimo = "HTTP %d" % erro.code
            valor = erro.headers.get("Retry-After", "")
            if valor.isdigit():
                espera = int(valor)
            if espera > espera_maxima:
                raise ErroFonte("%s pediu para esperar %d s" % (host, espera))
        except (urllib.error.URLError, OSError) as erro:
            ultimo = type(erro).__name__
        if tentativa < tentativas - 1:
            time.sleep(espera)
    raise ErroFonte("%s não respondeu (%s)" % (host, ultimo))


def pedir_json(url, cabecalhos=None, corpo=None):
    estado, dados = pedir(url, cabecalhos, corpo)
    try:
        return estado, json.loads(dados.decode("utf-8"))
    except ValueError:
        return estado, None


# --- Spotify -----------------------------------------------------------------

class Spotify:
    API = "https://api.spotify.com/v1"
    TOKEN = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.acesso = None

    def renovar(self):
        credenciais = base64.b64encode(
            ("%s:%s" % (self.client_id, self.client_secret)).encode()
        ).decode()
        corpo = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        ).encode()
        estado, dados = pedir_json(
            self.TOKEN,
            {"Authorization": "Basic " + credenciais,
             "Content-Type": "application/x-www-form-urlencoded"},
            corpo,
        )
        if estado != 200 or not dados or "access_token" not in dados:
            raise ErroFonte("Spotify recusou o refresh token (HTTP %d)" % estado)
        self.acesso = dados["access_token"]

    def get(self, url):
        if not self.acesso:
            self.renovar()
        estado, dados = pedir_json(url, {"Authorization": "Bearer " + self.acesso})
        if estado == 401:
            self.renovar()
            estado, dados = pedir_json(url, {"Authorization": "Bearer " + self.acesso})
        if estado != 200 or dados is None:
            raise ErroFonte("Spotify devolveu HTTP %d" % estado)
        return dados

    def artistas_seguidos(self):
        """GET /me/following continua ativo depois das mudanças de fevereiro de 2026."""
        url = self.API + "/me/following?type=artist&limit=50"
        artistas = []
        while url:
            pagina = self.get(url)["artists"]
            for item in pagina.get("items") or []:
                artistas.append({
                    "nome": item["name"],
                    "spotify_id": item["id"],
                    "spotify_url": (item.get("external_urls") or {}).get("spotify"),
                    # "genres" está marcado como obsoleto: pode deixar de vir.
                    "generos_spotify": item.get("genres") or [],
                })
            url = pagina.get("next")
        return artistas

    def albuns(self, artista_id, paginas=3):
        """Lançamentos de um artista. A API limita agora a 10 por página."""
        limite = 10
        for pagina in range(paginas):
            url = "%s/artists/%s/albums?include_groups=album,single&limit=%d&offset=%d" % (
                self.API, urllib.parse.quote(artista_id), limite, pagina * limite)
            itens = self.get(url).get("items") or []
            for item in itens:
                yield item
            if len(itens) < limite:
                return


def ler_lista(ficheiro):
    """Linhas com conteúdo de um ficheiro de texto, sem comentários."""
    linhas = []
    for linha in ficheiro.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            linhas.append(linha)
    return linhas


def obter_bandas(avisos):
    """Devolve (bandas, origem, spotify). Plano B: bands.txt."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    segredo = os.environ.get("SPOTIFY_CLIENT_SECRET")
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    spotify = None
    if client_id and segredo and refresh:
        spotify = Spotify(client_id, segredo, refresh)
        try:
            artistas = spotify.artistas_seguidos()
            if artistas:
                return sem_repetidas(artistas), "spotify", spotify
            avisos.append("O Spotify não devolveu artistas seguidos. Foi usada a lista bands.txt.")
        except (ErroFonte, KeyError, TypeError) as erro:
            avisos.append("Spotify indisponível (%s). Foi usada a lista bands.txt." % erro)
        spotify = None
    else:
        avisos.append("Spotify por ligar. Foi usada a lista bands.txt.")
    bandas = [{"nome": nome, "spotify_id": None, "spotify_url": None, "generos_spotify": []}
              for nome in ler_lista(FICHEIRO_BANDAS)]
    return sem_repetidas(bandas), "bands.txt", spotify


def sem_repetidas(bandas):
    vistas = {}
    for banda in bandas:
        vistas.setdefault(chave_banda(banda["nome"]), banda)
    return list(vistas.values())


# --- MusicBrainz --------------------------------------------------------------

class MusicBrainz:
    API = "https://musicbrainz.org/ws/2"
    INTERVALO = 1.1  # limite oficial: 1 pedido por segundo

    def __init__(self):
        self.ultimo = 0.0

    def get(self, caminho, parametros):
        falta = self.ultimo + self.INTERVALO - time.monotonic()
        if falta > 0:
            time.sleep(falta)
        parametros = dict(parametros, fmt="json")
        url = "%s/%s?%s" % (self.API, caminho, urllib.parse.urlencode(parametros, safe="+|"))
        try:
            estado, dados = pedir_json(url)
        finally:
            self.ultimo = time.monotonic()
        if estado == 404:
            return None
        if estado != 200 or dados is None:
            raise ErroFonte("MusicBrainz devolveu HTTP %d" % estado)
        return dados

    def id_por_spotify(self, spotify_id):
        """A ligação direta ao Spotify evita confundir bandas com o mesmo nome."""
        dados = self.get("url", {
            "resource": "https://open.spotify.com/artist/" + spotify_id,
            "inc": "artist-rels",
        })
        for relacao in (dados or {}).get("relations") or []:
            artista = relacao.get("artist")
            if artista and artista.get("id"):
                return artista["id"], artista.get("type")
        return None, None

    def id_por_nome(self, nome, so_grupos=False):
        """Só aceita nome exato. Entre homónimos, prefere quem tem etiquetas de metal."""
        protegido = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", nome)
        dados = self.get("artist", {"query": 'artist:"%s"' % protegido, "limit": 10}) or {}
        alvo = chave_banda(nome)
        melhor, melhor_nota = None, None
        for artista in dados.get("artists") or []:
            nomes = [artista.get("name", ""), artista.get("sort-name", "")]
            nomes += [a.get("name", "") for a in artista.get("aliases") or []]
            if alvo not in {chave_banda(n) for n in nomes if n}:
                continue
            if so_grupos and artista.get("type") != "Group":
                continue
            etiquetas = artista.get("tags") or []
            metal = sum(max(t.get("count", 1), 1) for t in etiquetas if "metal" in t.get("name", ""))
            nota = (metal, len(etiquetas), artista.get("score", 0))
            if melhor_nota is None or nota > melhor_nota:
                melhor, melhor_nota = artista["id"], nota
        return melhor

    def etiquetas(self, mbid):
        dados = self.get("artist/" + mbid, {"inc": "genres+tags"}) or {}
        contagens = {}
        for item in (dados.get("genres") or []) + (dados.get("tags") or []):
            nome = item.get("name")
            if nome:
                contagens[nome] = max(contagens.get(nome, 0), max(item.get("count", 1), 1))
        return contagens

    def grupos_de_lancamentos(self, mbid):
        grupos, deslocamento = [], 0
        while True:
            dados = self.get("release-group", {
                "artist": mbid, "type": "album|ep", "limit": 100, "offset": deslocamento,
            }) or {}
            pagina = dados.get("release-groups") or []
            grupos += pagina
            deslocamento += len(pagina)
            if not pagina or deslocamento >= dados.get("release-group-count", 0) or deslocamento >= 500:
                return grupos


def data_valida(texto):
    """Primeiro dia possível de uma data do MusicBrainz, ou None se não for fiável (só o ano)."""
    if not texto or len(texto) < 7:
        return None
    try:
        return date.fromisoformat(texto if len(texto) == 10 else texto + "-01")
    except ValueError:
        return None


def e_recente(texto, hoje):
    dia = data_valida(texto)
    return dia is not None and hoje - timedelta(days=DIAS_RECENTES) <= dia <= hoje


def link_procura_spotify(banda, titulo):
    return "https://open.spotify.com/search/" + urllib.parse.quote("%s %s" % (banda, titulo))


def converter_lancamentos(nome_banda, grupos):
    lancamentos = []
    for grupo in grupos:
        secundarios = set(grupo.get("secondary-types") or [])
        tipo = grupo.get("primary-type")
        data = grupo.get("first-release-date") or ""
        if secundarios & TIPOS_SECUNDARIOS_EXCLUIDOS or tipo not in ("Album", "EP") or not data:
            continue
        lancamentos.append({
            "id": grupo["id"],
            "titulo": grupo.get("title", ""),
            "tipo": "EP" if tipo == "EP" else "Álbum",
            "data": data,
            "capa": "https://coverartarchive.org/release-group/%s/front-250" % grupo["id"],
            "spotify": link_procura_spotify(nome_banda, grupo.get("title", "")),
        })
    lancamentos.sort(key=lambda l: l["data"], reverse=True)
    return lancamentos[:MAX_LANCAMENTOS_POR_BANDA]


def so_nao_musica(grupos):
    """Verdadeiro se o artista só tem audiolivros, entrevistas e afins."""
    return bool(grupos) and all(
        set(g.get("secondary-types") or []) & TIPOS_NAO_MUSICA for g in grupos
    )


def carregar_cache():
    try:
        return json.loads(FICHEIRO_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_cache(cache):
    FICHEIRO_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def entrada_mb(mb, cache, banda, hoje):
    """Devolve a entrada da cache da banda. O ID encontrado não volta a ser procurado."""
    chave = chave_banda(banda["nome"])
    entrada = cache.get(chave) or {}
    verificado = data_valida(entrada.get("verificado", ""))
    fresca = verificado and (hoje - verificado).days < DIAS_CACHE
    if fresca and "etiquetas" in entrada:
        return entrada
    mbid = entrada.get("mbid")
    if not mbid and banda.get("spotify_id"):
        mbid, tipo = mb.id_por_spotify(banda["spotify_id"])
        # A ligação às vezes aponta para a pessoa e não para a banda com o mesmo nome
        # (Marilyn Manson). Só troca se a pessoa não tiver álbuns: os artistas a solo ficam.
        if mbid and tipo == "Person" and not converter_lancamentos(
                banda["nome"], mb.grupos_de_lancamentos(mbid)):
            mbid = mb.id_por_nome(banda["nome"], so_grupos=True) or mbid
    if not mbid:
        mbid = mb.id_por_nome(banda["nome"])
    entrada.update({
        "nome": banda["nome"],
        "mbid": mbid,
        "etiquetas": mb.etiquetas(mbid) if mbid else {},
        "verificado": hoje.isoformat(),
    })
    cache[chave] = entrada
    return entrada


# --- Categorias ----------------------------------------------------------------

def carregar_categorias(avisos):
    try:
        dados = json.loads(FICHEIRO_CATEGORIAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        avisos.append("O ficheiro categorias.json tem um erro e foi ignorado.")
        return {}
    validas = set(CATEGORIAS) | {OUTROS, IGNORAR}
    forcadas = {}
    for nome, categoria in dados.items():
        if nome.startswith("_"):
            continue
        if categoria in validas:
            forcadas[chave_banda(nome)] = categoria
        else:
            avisos.append('Categoria inválida em categorias.json: "%s" (%s).' % (categoria, nome))
    return forcadas


def categoria_da_etiqueta(etiqueta):
    texto = normalizar(etiqueta)
    for padrao, categoria in REGRAS_CATEGORIA:
        if re.search(r"\b(?:%s)\b" % padrao, texto):
            return categoria
    return None


def categoria_por_regras(etiquetas_mb, generos_spotify):
    """Soma os votos das etiquetas por categoria. Sem correspondência, fica em Outros."""
    pontos = {}
    for etiqueta, votos in etiquetas_mb.items():
        categoria = categoria_da_etiqueta(etiqueta)
        if categoria:
            pontos[categoria] = pontos.get(categoria, 0) + max(votos, 1)
    for genero in generos_spotify:
        categoria = categoria_da_etiqueta(genero)
        if categoria:
            pontos[categoria] = pontos.get(categoria, 0) + 1
    if not pontos:
        return OUTROS
    melhor = max(CATEGORIAS, key=lambda c: (pontos.get(c, 0), -CATEGORIAS.index(c)))
    if etiquetas_mb and pontos[melhor] < FRACAO_MINIMA_CATEGORIA * max(etiquetas_mb.values()):
        return OUTROS
    return melhor


def nao_e_musica(generos):
    return any(GENEROS_NAO_MUSICA.search(normalizar(g)) for g in generos)


# --- Bandsintown ----------------------------------------------------------------

PAIS_PORTUGAL = {"portugal", "pt", "prt"}


def nome_para_bandsintown(nome):
    """A documentação pede estes caracteres duplamente codificados."""
    texto = urllib.parse.quote(nome, safe="")
    return (texto.replace("%2F", "%252F").replace("%3F", "%253F")
            .replace("%2A", "%252A").replace("%22", "%27C"))


def filtrar_concertos(eventos, hoje):
    concertos = []
    for evento in eventos if isinstance(eventos, list) else []:
        sala = evento.get("venue") or {}
        quando = evento.get("datetime") or ""
        if normalizar(sala.get("country") or "") not in PAIS_PORTUGAL or quando[:10] < hoje.isoformat():
            continue
        bilhetes = next((o.get("url") for o in evento.get("offers") or [] if o.get("url")), None)
        concertos.append({
            "id": str(evento.get("id") or quando + sala.get("name", "")),
            "data": quando,
            "cidade": sala.get("city") or "",
            "sala": sala.get("name") or "",
            "bilhetes": bilhetes or evento.get("url"),
            "tem_bilhetes": bool(bilhetes),
        })
    concertos.sort(key=lambda c: c["data"])
    return concertos


class Bandsintown:
    API = "https://rest.bandsintown.com"

    def __init__(self, app_id):
        self.app_id = app_id

    def concertos(self, nome, hoje):
        """Devolve os concertos em Portugal. Erro de autorização levanta PermissionError."""
        url = "%s/artists/%s/events/?%s" % (
            self.API, nome_para_bandsintown(nome),
            urllib.parse.urlencode({"app_id": self.app_id, "date": "upcoming"}))
        estado, dados = pedir_json(url)
        time.sleep(0.2)
        if estado in (401, 403):
            raise PermissionError("Bandsintown recusou o app_id (HTTP %d)" % estado)
        if estado == 404:
            return []
        if estado != 200:
            raise ErroFonte("Bandsintown devolveu HTTP %d" % estado)
        return filtrar_concertos(dados, hoje)


# --- Notícias -------------------------------------------------------------------

def nome_local(elemento):
    return elemento.tag.rsplit("}", 1)[-1].lower()


def ler_data(texto):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        resultado = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        try:
            resultado = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            return None
    if resultado is None:
        return None
    if resultado.tzinfo is None:
        resultado = resultado.replace(tzinfo=timezone.utc)
    return resultado.astimezone(timezone.utc)


def limpar_texto(texto):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(texto or ""))).strip()


def analisar_feed(dados, fonte):
    """Lê RSS ou Atom. Só devolve artigos com título, ligação http(s) e data."""
    if b"<!ENTITY" in dados:
        raise ValueError("feed com entidades XML")
    raiz = ET.fromstring(dados)
    artigos = []
    for item in raiz.iter():
        if nome_local(item) not in ("item", "entry"):
            continue
        titulo, link, quando = "", "", None
        for filho in item:
            nome = nome_local(filho)
            if nome == "title":
                titulo = limpar_texto("".join(filho.itertext()))
            elif nome == "link":
                candidato = filho.get("href") if filho.get("href") else (filho.text or "")
                if filho.get("rel", "alternate") == "alternate" and not link:
                    link = candidato.strip()
            elif nome in ("pubdate", "published", "date", "updated"):
                if quando is None or nome != "updated":
                    quando = ler_data(filho.text) or quando
        if titulo and link.startswith(("http://", "https://")) and quando:
            artigos.append({"titulo": titulo, "link": link, "fonte": fonte, "data": quando})
    return artigos


def obter_noticias(anteriores, avisos):
    limite = agora() - timedelta(days=DIAS_NOTICIAS)
    futuro = agora() + timedelta(days=1)
    artigos, estados = [], []
    for linha in ler_lista(FICHEIRO_FEEDS):
        if "|" not in linha:
            avisos.append("Linha inválida em feeds.txt: %s" % linha)
            continue
        fonte, url = [parte.strip() for parte in linha.split("|", 1)]
        try:
            estado, dados = pedir(url, {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            if estado != 200:
                raise ErroFonte("HTTP %d" % estado)
            lidos = analisar_feed(dados, fonte)
            if not lidos:
                raise ErroFonte("sem artigos")
            validos = [a for a in lidos if limite <= a["data"] <= futuro]
            validos.sort(key=lambda a: a["data"], reverse=True)
            for artigo in validos[:MAX_NOTICIAS_POR_FEED]:
                artigos.append(dict(artigo, data=artigo["data"].isoformat()))
            estados.append({"nome": fonte, "ok": True, "artigos": min(len(validos), MAX_NOTICIAS_POR_FEED)})
        except (ErroFonte, ET.ParseError, ValueError) as erro:
            print("Feed %s falhou: %s" % (fonte, erro))
            # Mantém as notícias anteriores dessa fonte até voltar a funcionar.
            guardados = [a for a in anteriores if a.get("fonte") == fonte
                         and (ler_data(a.get("data")) or limite) > limite]
            artigos += guardados
            estados.append({"nome": fonte, "ok": False, "artigos": len(guardados)})
    vistos, unicos = set(), []
    for artigo in sorted(artigos, key=lambda a: a["data"], reverse=True):
        if artigo["link"] not in vistos:
            vistos.add(artigo["link"])
            unicos.append(artigo)
    falhados = [e["nome"] for e in estados if not e["ok"]]
    if falhados:
        avisos.append("Feeds sem resposta nesta atualização: %s." % ", ".join(falhados))
    return unicos[:MAX_NOTICIAS], estados


# --- Juntar tudo -----------------------------------------------------------------

def juntar_spotify(spotify, spotify_id, banda, hoje):
    """Nos lançamentos recentes, troca a procura pelo link e pela capa do próprio álbum."""
    recentes = [l for l in banda["lancamentos"] if e_recente(l["data"], hoje)]
    if not recentes:
        return
    por_titulo = {normalizar(l["titulo"]): l for l in recentes}
    for album in spotify.albuns(spotify_id):
        lancamento = por_titulo.pop(normalizar(album.get("name", "")), None)
        if lancamento:
            imagens = album.get("images") or []
            if imagens:
                lancamento["capa"] = imagens[min(1, len(imagens) - 1)]["url"]
            link = (album.get("external_urls") or {}).get("spotify")
            if link:
                lancamento["spotify"] = link
        if not por_titulo:
            return


def carregar_dados_anteriores():
    try:
        dados = json.loads(FICHEIRO_DADOS.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def processar_bandas(bandas, spotify, avisos):
    hoje = agora().date()
    anteriores = {b["id"]: b for b in carregar_dados_anteriores().get("bandas", [])}
    cache = carregar_cache()
    forcadas = carregar_categorias(avisos)
    mb = MusicBrainz()
    app_id = os.environ.get("BANDSINTOWN_APP_ID")
    bit = Bandsintown(app_id) if app_id else None
    estado_concertos = "ok" if bit else "sem_app_id"
    if not bit:
        avisos.append("Concertos por ativar: falta o app_id da Bandsintown.")

    resultado, excluidas = [], []
    falhas_mb = falhas_bit = 0
    spotify_ativo = spotify is not None
    bandas = [b for b in bandas if forcadas.get(chave_banda(b["nome"])) != IGNORAR]

    try:
        for indice, banda in enumerate(bandas, 1):
            nome = banda["nome"]
            chave = chave_banda(nome)
            antes = anteriores.get(chave, {})
            forcada = forcadas.get(chave)
            print("[%d/%d] %s" % (indice, len(bandas), nome), flush=True)

            if not forcada and nao_e_musica(banda["generos_spotify"]):
                excluidas.append(nome)
                continue

            etiquetas, mbid, grupos = {}, None, None
            mb_falhou = False
            try:
                entrada = entrada_mb(mb, cache, banda, hoje)
                etiquetas, mbid = entrada.get("etiquetas") or {}, entrada.get("mbid")
                if mbid:
                    grupos = mb.grupos_de_lancamentos(mbid)
            except ErroFonte as erro:
                print("  MusicBrainz: %s" % erro)
                falhas_mb += 1
                mb_falhou = True

            # As etiquetas do MusicBrainz são livres (o Saor tem "poetry"): sozinhas não chegam,
            # é preciso também não haver álbuns nem EPs de música.
            sem_discos = grupos is not None and not converter_lancamentos(nome, grupos)
            if not forcada and (so_nao_musica(grupos or []) or (nao_e_musica(etiquetas) and sem_discos)):
                excluidas.append(nome)
                continue

            # Sem correspondência em lado nenhum: confirma se há música no Spotify.
            if (not forcada and not mbid and not mb_falhou and spotify_ativo
                    and banda.get("spotify_id") and not banda["generos_spotify"]):
                try:
                    if next(spotify.albuns(banda["spotify_id"], paginas=1), None) is None:
                        excluidas.append(nome)
                        continue
                except ErroFonte as erro:
                    print("  Spotify: %s" % erro)

            lancamentos = (converter_lancamentos(nome, grupos) if grupos is not None
                           else antes.get("lancamentos", []))
            registo = {
                "id": chave,
                "nome": nome,
                "categoria": forcada or categoria_por_regras(etiquetas, banda["generos_spotify"]),
                "spotify": banda.get("spotify_url") or "https://open.spotify.com/search/" + urllib.parse.quote(nome),
                "lancamentos": lancamentos,
                "concertos": [],
            }

            if spotify_ativo and banda.get("spotify_id"):
                try:
                    juntar_spotify(spotify, banda["spotify_id"], registo, hoje)
                except ErroFonte as erro:
                    print("  Spotify: %s" % erro)
                    avisos.append("Algumas capas e links do Spotify ficaram por obter (%s)." % erro)
                    spotify_ativo = False

            if bit:
                try:
                    registo["concertos"] = bit.concertos(nome, hoje)
                except PermissionError as erro:
                    print("  %s" % erro)
                    avisos.append("Concertos indisponíveis: a Bandsintown recusou o app_id.")
                    estado_concertos, bit = "recusado", None
                except ErroFonte as erro:
                    print("  Bandsintown: %s" % erro)
                    falhas_bit += 1
                    registo["concertos"] = [c for c in antes.get("concertos", [])
                                            if c.get("data", "")[:10] >= hoje.isoformat()]

            resultado.append(registo)
            if indice % 25 == 0:
                guardar_cache(cache)
    finally:
        # Mesmo com um erro inesperado, não se perdem as pesquisas já feitas.
        guardar_cache(cache)

    if falhas_mb:
        avisos.append("MusicBrainz falhou em %d bandas; ficaram os dados anteriores." % falhas_mb)
    if falhas_bit:
        avisos.append("Bandsintown falhou em %d bandas; ficaram os dados anteriores." % falhas_bit)
    fontes = {"concertos": estado_concertos, "excluidas": sorted(excluidas)}
    return resultado, fontes


def main():
    avisos = []
    bandas, origem, spotify = obter_bandas(avisos)
    if not bandas:
        print("Sem bandas: nem o Spotify nem o bands.txt devolveram nomes.", file=sys.stderr)
        return 1
    print("Bandas: %d (origem: %s)" % (len(bandas), origem), flush=True)

    registos, fontes = processar_bandas(bandas, spotify, avisos)
    anteriores = carregar_dados_anteriores().get("noticias", [])
    noticias, estado_feeds = obter_noticias(anteriores, avisos)
    fontes.update({"bandas": origem, "feeds": estado_feeds})

    dados = {
        "atualizado": agora().isoformat(timespec="seconds"),
        "fontes": fontes,
        "avisos": avisos,
        "bandas": registos,
        "noticias": noticias,
    }
    FICHEIRO_DADOS.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("Feito: %d bandas, %d notícias, %d avisos." % (len(registos), len(noticias), len(avisos)))
    for aviso in avisos:
        print("Aviso: " + aviso)
    return 0


if __name__ == "__main__":
    sys.exit(main())

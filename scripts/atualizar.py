#!/usr/bin/env python3
"""Vai buscar os dados às fontes e gera docs/data.json. Corre na GitHub Action.

Só usa a biblioteca padrão do Python, por isso não é preciso instalar nada.
As chaves vêm de variáveis de ambiente (GitHub Secrets) e nunca são escritas nos dados.
Sem chaves, corre na mesma: usa bands.txt.
"""
import base64
import difflib
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
FICHEIRO_MBID_MANUAL = RAIZ / "mbid_manual.json"
FICHEIRO_FESTIVAIS = RAIZ / "festivais.json"

FONTE_AGENDA = "Caminhos Metálicos"
URL_AGENDA = "https://www.caminhosmetalicos.com/agenda/"

USER_AGENT = "metal-app/1.0 (+https://github.com/Peter-app-7/metal)"
DIAS_RECENTES = 365
DIAS_CACHE = 30
MAX_LANCAMENTOS_POR_BANDA = 10  # além destes, ficam sempre todos os do último ano
# O MusicBrainz não ordena por data, por isso é preciso ler tudo. O Metallica tem mais de 1000 grupos.
MAX_GRUPOS_POR_BANDA = 3000
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
# Estas só existem por escolha em categorias.json: não têm regras por etiqueta.
CATEGORIAS_SO_MANUAIS = ["Progressivo", "Grunge"]

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

    def __init__(self, mensagem, temporario=True, espera=None):
        super().__init__(mensagem)
        self.temporario = temporario  # falso quando a fonte recusa mesmo o acesso
        self.espera = espera  # segundos que a fonte pediu para esperar


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
                raise ErroFonte("%s pediu para esperar %d s" % (host, espera), espera=espera)
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
            raise ErroFonte("Spotify recusou o refresh token (HTTP %d)" % estado, temporario=False)
        self.acesso = dados["access_token"]

    def get(self, url):
        if not self.acesso:
            self.renovar()
        estado, dados = pedir_json(url, {"Authorization": "Bearer " + self.acesso})
        if estado == 401:
            self.renovar()
            estado, dados = pedir_json(url, {"Authorization": "Bearer " + self.acesso})
        if estado != 200 or dados is None:
            raise ErroFonte("Spotify devolveu HTTP %d" % estado, temporario=False)
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


# Até quando o Spotify pediu para não lhe fazerem pedidos. Fica guardado em data.json.
ESPERA_SPOTIFY = {"ate": None}


def anotar_espera_spotify(erro):
    if erro.espera:
        ESPERA_SPOTIFY["ate"] = (agora() + timedelta(seconds=erro.espera)).isoformat(timespec="seconds")


def lista_guardada(anteriores):
    """A última lista vinda do Spotify, para uma falha temporária não trocar as bandas pelo plano B."""
    if (anteriores.get("fontes") or {}).get("bandas") not in ("spotify", "spotify_guardado"):
        return []
    bandas = []
    for registo in anteriores.get("bandas") or []:
        url = registo.get("spotify") or ""
        encontrado = re.search(r"/artist/(\w+)", url)
        bandas.append({
            "nome": registo["nome"],
            "spotify_id": encontrado.group(1) if encontrado else None,
            "spotify_url": url if encontrado else None,
            "generos_spotify": [],
        })
    return bandas


def obter_bandas(avisos):
    """Devolve (bandas, origem, spotify).

    Falha temporária (limite de pedidos, rede): usa a última lista do Spotify.
    Recusa de acesso, ou sem lista guardada: plano B, o ficheiro bands.txt.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    segredo = os.environ.get("SPOTIFY_CLIENT_SECRET")
    refresh = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    if client_id and segredo and refresh:
        anteriores = carregar_dados_anteriores()
        guardadas = lista_guardada(anteriores)
        espera_ate = ler_data((anteriores.get("fontes") or {}).get("spotify_espera_ate"))
        if espera_ate and espera_ate > agora():
            # Respeita o pedido do Spotify: durante a espera, não lhe faz nenhum pedido.
            ESPERA_SPOTIFY["ate"] = espera_ate.isoformat(timespec="seconds")
            if guardadas:
                avisos.append("O Spotify pediu para esperar. Foi usada a última lista de bandas.")
                return sem_repetidas(guardadas), "spotify_guardado", None
            avisos.append("O Spotify pediu para esperar. Foi usada a lista bands.txt.")
        else:
            spotify = Spotify(client_id, segredo, refresh)
            try:
                artistas = spotify.artistas_seguidos()
                if artistas:
                    return sem_repetidas(artistas), "spotify", spotify
                avisos.append("O Spotify não devolveu artistas seguidos. Foi usada a lista bands.txt.")
            except ErroFonte as erro:
                anotar_espera_spotify(erro)
                if erro.temporario and guardadas:
                    avisos.append("Spotify indisponível (%s). Foi usada a última lista de bandas." % erro)
                    return sem_repetidas(guardadas), "spotify_guardado", None
                avisos.append("Spotify indisponível (%s). Foi usada a lista bands.txt." % erro)
            except (KeyError, TypeError) as erro:
                avisos.append("Spotify indisponível (%s). Foi usada a lista bands.txt." % type(erro).__name__)
    else:
        avisos.append("Spotify por ligar. Foi usada a lista bands.txt.")
    bandas = [{"nome": nome, "spotify_id": None, "spotify_url": None, "generos_spotify": []}
              for nome in ler_lista(FICHEIRO_BANDAS)]
    return sem_repetidas(bandas), "bands.txt", None


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
            if not pagina or deslocamento >= dados.get("release-group-count", 0) or deslocamento >= MAX_GRUPOS_POR_BANDA:
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
    hoje = agora().date()
    return [l for indice, l in enumerate(lancamentos)
            if indice < MAX_LANCAMENTOS_POR_BANDA or e_recente(l["data"], hoje)]


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


def carregar_mbid_manual(avisos):
    """IDs indicados à mão em mbid_manual.json: têm prioridade sobre a pesquisa automática."""
    try:
        dados = json.loads(FICHEIRO_MBID_MANUAL.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError:
        avisos.append("O ficheiro mbid_manual.json tem um erro e foi ignorado.")
        return {}
    manuais = {}
    for nome, mbid in dados.items():
        if nome.startswith("_"):
            continue
        if isinstance(mbid, str) and re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", mbid.strip()):
            manuais[chave_banda(nome)] = mbid.strip().lower()
        else:
            avisos.append('ID inválido em mbid_manual.json: "%s" (%s).' % (mbid, nome))
    return manuais


def entrada_mb(mb, cache, banda, hoje, manuais=None):
    """Devolve a entrada da cache da banda. O ID encontrado não volta a ser procurado."""
    chave = chave_banda(banda["nome"])
    entrada = cache.get(chave) or {}
    manual = (manuais or {}).get(chave)
    # Um ID manual novo, alterado ou retirado obriga a refazer a entrada.
    if (manual and entrada.get("mbid") != manual) or bool(manual) != bool(entrada.get("manual")):
        entrada = {}
    verificado = data_valida(entrada.get("verificado", ""))
    fresca = verificado and (hoje - verificado).days < DIAS_CACHE
    if fresca and "etiquetas" in entrada:
        return entrada
    mbid = manual or entrada.get("mbid")
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
    if manual:
        entrada["manual"] = True
    cache[chave] = entrada
    return entrada


# --- Categorias ----------------------------------------------------------------

def carregar_categorias(avisos):
    try:
        dados = json.loads(FICHEIRO_CATEGORIAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        avisos.append("O ficheiro categorias.json tem um erro e foi ignorado.")
        return {}
    validas = set(CATEGORIAS) | set(CATEGORIAS_SO_MANUAIS) | {OUTROS, IGNORAR}
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


# --- Agenda do Caminhos Metálicos -----------------------------------------------

# Letras que a normalização Unicode não separa do acento ("Mgła", "Dødheimsgard").
LETRAS_ESPECIAIS = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe", "þ": "th", "ð": "d"})


def chave_comparacao(nome):
    """Nome para comparar bandas: minúsculas, sem acentos, pontuação nem "the" inicial."""
    texto = normalizar(nome.lower().translate(LETRAS_ESPECIAIS))
    sem_the = re.sub(r"^the\s+", "", texto)
    return sem_the or texto


def separar_bandas(texto, minhas=frozenset()):
    """Separa por "+", "w/" ou "&". O "&" fica se fizer parte do nome de uma banda seguida."""
    bandas = []
    for parte in re.split(r"\s*\+\s*|\s+w/\s*", texto, flags=re.IGNORECASE):
        parte = parte.strip()
        if not parte:
            continue
        if chave_comparacao(parte) in minhas:
            bandas.append(parte)
        else:
            bandas += [p.strip() for p in re.split(r"\s+&\s+", parte) if p.strip()]
    return bandas


def texto_da_pagina(pagina):
    """Texto da página HTML, com uma linha por parágrafo."""
    texto = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", pagina)
    texto = re.sub(r"(?i)<br\s*/?>|</(p|li|h[1-6]|div|tr)>", "\n", texto)
    texto = html.unescape(re.sub(r"<[^>]+>", "", texto)).replace("\xa0", " ")
    return [re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.splitlines()]


def ler_linha_agenda(linha, minhas=frozenset()):
    """Lê uma linha. Devolve None se não for um concerto no formato conhecido.

    Formas reais: "Banda + Banda – Sala (Cidade)", "Evento – Banda + Banda – Sala (Cidade)",
    "Banda – (Cidade)" e "Banda – Sala (Santa Cruz – Madeira)".
    """
    encontrado = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*[–—-]\s*(.+)$", linha)
    if not encontrado:
        return None
    dia, mes, ano, resto = encontrado.groups()
    try:
        data = date(int(ano), int(mes), int(dia))
    except ValueError:
        return None
    # A cidade sai primeiro, porque pode ter um traço lá dentro.
    cidade = ""
    parenteses = re.search(r"\(([^()]*)\)\s*$", resto)
    if parenteses:
        cidade = parenteses.group(1).strip()
        resto = resto[:parenteses.start()]
    partes = [p.strip() for p in re.split(r"\s*[–—]\s*|\s+-\s+|\s+-$", resto)]
    tem_sala_ou_cidade = len(partes) >= 2 or bool(cidade)
    if not tem_sala_ou_cidade or not partes[0]:
        return None
    evento, sala = "", ""
    if len(partes) == 1:
        bandas_texto = partes[0]
    elif len(partes) == 2:
        bandas_texto, sala = partes
    else:
        evento, sala = partes[0], partes[-1]
        bandas_texto = " – ".join(partes[1:-1])
    bandas = separar_bandas(bandas_texto, minhas)
    if not bandas:
        return None
    return {"data": data.isoformat(), "bandas": bandas, "sala": sala, "cidade": cidade, "evento": evento}


def analisar_agenda(pagina, minhas=frozenset()):
    """Devolve (concertos lidos, linhas com data que não se perceberam, data de atualização)."""
    concertos, rejeitadas, atualizada = [], [], None
    for linha in texto_da_pagina(pagina):
        marca = re.search(r"Atuali[sz]ado a\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", linha)
        if marca and not atualizada:
            try:
                atualizada = date(int(marca.group(3)), int(marca.group(2)), int(marca.group(1))).isoformat()
            except ValueError:
                pass
        if not re.match(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b", linha):
            continue
        concerto = ler_linha_agenda(linha, minhas)
        if concerto:
            concertos.append(concerto)
        else:
            rejeitadas.append(linha[:150])
    return concertos, rejeitadas, atualizada


def mapa_das_minhas_bandas(registos):
    return {chave_comparacao(r["nome"]): r for r in registos}


def concerto_da_agenda(lido, minhas):
    """Transforma uma linha lida num concerto, ou None se não tiver nenhuma banda seguida."""
    bandas, categorias = [], []
    for nome in lido["bandas"]:
        registo = minhas.get(chave_comparacao(nome))
        bandas.append({"nome": nome, "minha": bool(registo)})
        if registo and registo["categoria"] not in categorias:
            categorias.append(registo["categoria"])
    if not categorias:
        return None
    return {
        "id": "cm-%s-%s-%s" % (lido["data"], normalizar(lido["cidade"]), chave_comparacao(lido["bandas"][0])),
        "data": lido["data"],
        "bandas": bandas,
        "categorias": categorias,
        "sala": lido["sala"],
        "cidade": lido["cidade"],
        "evento": lido.get("evento", ""),
        "fontes": [FONTE_AGENDA],
        "link": URL_AGENDA,
    }


def quase_correspondencias(lidos, minhas):
    """Nomes da agenda parecidos com uma banda seguida, mas que não foram aceites."""
    pares = {}
    for nome in {n for lido in lidos for n in lido["bandas"]}:
        chave = chave_comparacao(nome)
        if chave in minhas or len(chave) < 3:
            continue
        palavras = " %s " % chave
        for minha, registo in minhas.items():
            pequena, grande = sorted((chave, minha), key=len)
            palavra_inteira = len(pequena) >= 4 and " %s " % pequena in " %s " % grande
            parecida = (min(len(chave), len(minha)) >= 4 and difflib.SequenceMatcher(
                None, chave.replace(" ", ""), minha.replace(" ", "")).ratio() >= 0.84)
            if palavra_inteira or parecida:
                pares[(nome, registo["nome"])] = True
    return [{"agenda": a, "banda": b} for a, b in sorted(pares)]


def obter_agenda(minhas, anteriores, hoje, avisos, lidos_saida=None):
    """Lê a agenda uma vez. Se falhar, mantém os concertos guardados e nunca rebenta.

    Em lidos_saida ficam as linhas acabadas de ler, que também servem para os festivais.
    """
    estado = {"estado": "ok", "linhas": 0, "futuras": 0, "rejeitadas": [], "quase": [], "atualizada": None}
    erro = None
    lidos = []
    try:
        codigo, dados = pedir(URL_AGENDA, tentativas=1)
        if codigo != 200:
            erro = "HTTP %d" % codigo
        else:
            lidos, estado["rejeitadas"], estado["atualizada"] = analisar_agenda(
                dados.decode("utf-8", "replace"), frozenset(minhas))
            if not lidos:
                erro = "nenhuma linha lida: o formato da página pode ter mudado"
    except Exception as falha:  # uma falha desta fonte nunca pode parar a Action
        erro = str(falha) if isinstance(falha, ErroFonte) else type(falha).__name__

    if erro:
        print("Agenda: %s" % erro)
        avisos.append("Agenda do Caminhos Metálicos indisponível (%s). Ficaram os concertos guardados." % erro)
        estado.update({"estado": "indisponivel", "erro": erro})
        lidos = [{"data": c["data"][:10], "bandas": [b["nome"] for b in c["bandas"]], "sala": c.get("sala", ""),
                  "cidade": c.get("cidade", ""), "evento": c.get("evento", "")} for c in anteriores]
    else:
        if lidos_saida is not None:
            lidos_saida.extend(lidos)
        estado["linhas"] = len(lidos)
        estado["quase"] = quase_correspondencias(lidos, minhas)
        if estado["rejeitadas"]:
            print("Agenda: %d linhas não percebidas" % len(estado["rejeitadas"]))
            for linha in estado["rejeitadas"]:
                print("  " + linha)

    futuros = [l for l in lidos if l["data"] >= hoje.isoformat()]
    if not erro:
        estado["futuras"] = len(futuros)
    concertos = [c for c in (concerto_da_agenda(l, minhas) for l in futuros) if c]
    if not erro:
        estado["minhas"] = len(concertos)
    return concertos, estado


def concertos_sem_repetidos(concertos):
    """Mesma data, mesma cidade e uma banda em comum é o mesmo concerto. Ordena por data."""
    resultado, indice = [], {}
    for concerto in concertos:
        chaves = [(concerto["data"][:10], normalizar(concerto["cidade"]), chave_comparacao(b["nome"]))
                  for b in concerto["bandas"]]
        existente = next((indice[c] for c in chaves if c in indice), None)
        if existente is None:
            existente = dict(concerto, fontes=list(concerto["fontes"]))
            resultado.append(existente)
        else:
            for categoria in concerto["categorias"]:
                if categoria not in existente["categorias"]:
                    existente["categorias"].append(categoria)
        for chave in chaves:
            indice.setdefault(chave, existente)
    resultado.sort(key=lambda c: c["data"])
    return resultado


# --- Festivais -------------------------------------------------------------------

# "fest" conta como palavra ou no fim de uma palavra (Moonfest), mas não em nomes de bandas comuns.
PALAVRAS_FESTIVAL = re.compile(r"\b(?:\w*fest|festival|open air|gathering|weekend|assembleia)\b")
FALSOS_FESTIVAIS = {"manifest", "infest"}


def e_nome_de_festival(nome):
    return any(m.group() not in FALSOS_FESTIVAIS for m in PALAVRAS_FESTIVAL.finditer(normalizar(nome)))


def marcar_bandas(nomes, minhas):
    """Lista de bandas sem repetidas, com as seguidas marcadas. Compara por nome completo."""
    vistas, bandas = set(), []
    for nome in nomes:
        chave = chave_comparacao(nome)
        if chave not in vistas:
            vistas.add(chave)
            bandas.append({"nome": nome, "minha": chave in minhas})
    return bandas


def novo_festival(nome, inicio, fim, local, cidade, link, nomes_bandas, fonte, minhas):
    bandas = marcar_bandas(nomes_bandas, minhas)
    return {
        "id": "fest-%s-%s" % (inicio, chave_comparacao(nome)),
        "nome": nome,
        "data_inicio": inicio,
        "data_fim": fim,
        "local": local,
        "cidade": cidade,
        "link": link,
        "bandas": bandas,
        "tem_minhas": any(b["minha"] for b in bandas),
        "fontes": [fonte],
    }


def festivais_da_agenda(lidos, minhas):
    """Procura festivais nas linhas da agenda. Os dias seguidos juntam-se num só festival.

    Critérios: palavra de festival no nome do evento ou numa "banda" que não seja seguida,
    ou um nome sozinho repetido em dias seguidos na mesma sala (o formato não distingue
    uma banda sozinha de um festival sem cartaz, por isso um só dia não chega).
    """
    def um_dia_depois(a, b):
        return (date.fromisoformat(b) - date.fromisoformat(a)).days == 1

    dias_por_nome = {}
    for lido in lidos:
        if not lido.get("evento") and len(lido["bandas"]) == 1:
            chave = (chave_comparacao(lido["bandas"][0]), normalizar(lido["sala"]), normalizar(lido["cidade"]))
            dias_por_nome.setdefault(chave, set()).add(lido["data"])

    def repetido_em_dias_seguidos(lido):
        chave = (chave_comparacao(lido["bandas"][0]), normalizar(lido["sala"]), normalizar(lido["cidade"]))
        dias = dias_por_nome.get(chave, set())
        return any(um_dia_depois(lido["data"], d) or um_dia_depois(d, lido["data"]) for d in dias)

    linhas = []
    for lido in lidos:
        evento = lido.get("evento") or ""
        nome, cartaz = None, lido["bandas"]
        if evento and e_nome_de_festival(evento):
            nome = evento
        else:
            for banda in lido["bandas"]:
                if chave_comparacao(banda) not in minhas and e_nome_de_festival(banda):
                    nome, cartaz = banda, [b for b in lido["bandas"] if b != banda]
                    break
        if (not nome and not evento and len(lido["bandas"]) == 1
                and chave_comparacao(lido["bandas"][0]) not in minhas and repetido_em_dias_seguidos(lido)):
            nome, cartaz = lido["bandas"][0], []
        if nome:
            linhas.append((chave_comparacao(nome), normalizar(lido["cidade"]), lido["data"], nome, cartaz, lido))

    festivais = []
    grupo_atual = {}
    for chave, cidade, data, nome, cartaz, lido in sorted(linhas, key=lambda l: (l[0], l[1], l[2])):
        anterior = grupo_atual.get((chave, cidade))
        if anterior and (data == anterior["data_fim"] or um_dia_depois(anterior["data_fim"], data)):
            anterior["data_fim"] = data
            nomes = [b["nome"] for b in anterior["bandas"]] + cartaz
            anterior["bandas"] = marcar_bandas(nomes, minhas)
            anterior["tem_minhas"] = any(b["minha"] for b in anterior["bandas"])
        else:
            novo = novo_festival(nome, data, data, lido["sala"], lido["cidade"], URL_AGENDA, cartaz, FONTE_AGENDA, minhas)
            grupo_atual[(chave, cidade)] = novo
            festivais.append(novo)
    return festivais


def festivais_manuais(minhas, avisos):
    """Lê festivais.json. Um erro numa entrada só tira essa entrada, com um aviso."""
    try:
        dados = json.loads(FICHEIRO_FESTIVAIS.read_text(encoding="utf-8"))
    except OSError:
        return []
    except ValueError:
        avisos.append("O ficheiro festivais.json tem um erro e foi ignorado.")
        return []
    entradas = dados.get("festivais", []) if isinstance(dados, dict) else dados
    festivais = []
    for entrada in entradas if isinstance(entradas, list) else []:
        try:
            nome = entrada["nome"].strip()
            inicio = date.fromisoformat(entrada["data_inicio"]).isoformat()
            fim = date.fromisoformat(entrada.get("data_fim") or inicio).isoformat()
            bandas = [b.strip() for b in entrada.get("bandas") or [] if b.strip()]
            if not nome or fim < inicio:
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            rotulo = entrada.get("nome") if isinstance(entrada, dict) else entrada
            avisos.append("Entrada inválida em festivais.json: %s." % rotulo)
            continue
        link = str(entrada.get("link") or "").strip()
        if link and not link.startswith(("https://", "http://")):
            avisos.append("Link inválido em festivais.json: %s." % nome)
            link = ""
        festivais.append(novo_festival(nome, inicio, fim, str(entrada.get("local") or "").strip(),
                                       str(entrada.get("cidade") or "").strip(), link, bandas, "Manual", minhas))
    return festivais


def mesmo_festival(a, b):
    """Datas que se tocam e o mesmo nome, ou um nome contido no outro em palavras inteiras."""
    if a["data_inicio"] > b["data_fim"] or b["data_inicio"] > a["data_fim"]:
        return False
    x, y = sorted((chave_comparacao(a["nome"]), chave_comparacao(b["nome"])), key=len)
    return x == y or (len(x) >= 5 and " %s " % x in " %s " % y)


def juntar_festivais(manuais, da_agenda, hoje, minhas):
    """Junta as duas fontes sem repetidos. O que foi escrito à mão tem prioridade."""
    resultado = []
    for festival in list(manuais) + list(da_agenda):
        igual = next((f for f in resultado if mesmo_festival(f, festival)), None)
        if igual is None:
            resultado.append(dict(festival, fontes=list(festival["fontes"])))
            continue
        for campo in ("local", "cidade", "link"):
            igual[campo] = igual[campo] or festival[campo]
        igual["data_inicio"] = min(igual["data_inicio"], festival["data_inicio"])
        igual["data_fim"] = max(igual["data_fim"], festival["data_fim"])
        igual["bandas"] = marcar_bandas([b["nome"] for b in igual["bandas"] + festival["bandas"]], minhas)
        igual["tem_minhas"] = any(b["minha"] for b in igual["bandas"])
        igual["fontes"] += [f for f in festival["fontes"] if f not in igual["fontes"]]
    futuros = [f for f in resultado if f["data_fim"] >= hoje.isoformat()]
    futuros.sort(key=lambda f: (f["data_inicio"], f["data_fim"], normalizar(f["nome"])))
    return futuros


def obter_festivais(lidos, agenda_ok, guardados, minhas, hoje, avisos):
    """Devolve (festivais a mostrar, festivais da agenda a guardar).

    Com a agenda em baixo, usa os últimos festivais guardados da agenda e o festivais.json.
    """
    if agenda_ok:
        da_agenda = festivais_da_agenda(lidos, minhas)
    else:
        da_agenda = []
        for festival in guardados or []:
            bandas = marcar_bandas([b["nome"] for b in festival.get("bandas", [])], minhas)
            da_agenda.append(dict(festival, bandas=bandas, tem_minhas=any(b["minha"] for b in bandas)))
    da_agenda = [f for f in da_agenda if f["data_fim"] >= hoje.isoformat()]
    return juntar_festivais(festivais_manuais(minhas, avisos), da_agenda, hoje, minhas), da_agenda


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
    # Os que já têm o link do próprio álbum não precisam de novo pedido.
    recentes = [l for l in banda["lancamentos"] if e_recente(l["data"], hoje) and "/album/" not in l["spotify"]]
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
    manuais = carregar_mbid_manual(avisos)
    mb = MusicBrainz()

    resultado, excluidas = [], []
    falhas_mb = 0
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
                entrada = entrada_mb(mb, cache, banda, hoje, manuais)
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
            # A capa e o link de um disco já encontrado no Spotify não mudam: reaproveitam-se.
            ja_encontrados = {l.get("id"): l for l in antes.get("lancamentos", [])
                              if "/album/" in l.get("spotify", "")}
            for lancamento in lancamentos:
                anterior = ja_encontrados.get(lancamento["id"])
                if anterior:
                    lancamento["capa"], lancamento["spotify"] = anterior["capa"], anterior["spotify"]
            registo = {
                "id": chave,
                "nome": nome,
                "categoria": forcada or categoria_por_regras(etiquetas, banda["generos_spotify"]),
                "spotify": banda.get("spotify_url") or "https://open.spotify.com/search/" + urllib.parse.quote(nome),
                "lancamentos": lancamentos,
            }

            if spotify_ativo and banda.get("spotify_id"):
                try:
                    juntar_spotify(spotify, banda["spotify_id"], registo, hoje)
                except ErroFonte as erro:
                    print("  Spotify: %s" % erro)
                    anotar_espera_spotify(erro)
                    avisos.append("Algumas capas e links do Spotify ficaram por obter (%s)." % erro)
                    spotify_ativo = False

            resultado.append(registo)
            if indice % 25 == 0:
                guardar_cache(cache)
    finally:
        # Mesmo com um erro inesperado, não se perdem as pesquisas já feitas.
        guardar_cache(cache)

    if falhas_mb:
        avisos.append("MusicBrainz falhou em %d bandas; ficaram os dados anteriores." % falhas_mb)
    fontes = {"excluidas": sorted(excluidas)}
    return resultado, fontes


def main():
    avisos = []
    bandas, origem, spotify = obter_bandas(avisos)
    if not bandas:
        print("Sem bandas: nem o Spotify nem o bands.txt devolveram nomes.", file=sys.stderr)
        return 1
    print("Bandas: %d (origem: %s)" % (len(bandas), origem), flush=True)

    registos, fontes = processar_bandas(bandas, spotify, avisos)
    dados_anteriores = carregar_dados_anteriores()
    minhas = mapa_das_minhas_bandas(registos)
    lidos = []
    concertos_agenda, fontes["agenda"] = obter_agenda(
        minhas, dados_anteriores.get("concertos", []), agora().date(), avisos, lidos)
    festivais, festivais_agenda = obter_festivais(
        lidos, fontes["agenda"]["estado"] == "ok", dados_anteriores.get("festivais_agenda"), minhas, agora().date(), avisos)
    concertos = concertos_sem_repetidos(concertos_agenda)
    anteriores = dados_anteriores.get("noticias", [])
    noticias, estado_feeds = obter_noticias(anteriores, avisos)
    fontes.update({"bandas": origem, "feeds": estado_feeds, "spotify_espera_ate": ESPERA_SPOTIFY["ate"]})

    dados = {
        "atualizado": agora().isoformat(timespec="seconds"),
        "fontes": fontes,
        "avisos": avisos,
        "bandas": registos,
        "concertos": concertos,
        "festivais": festivais,
        "festivais_agenda": festivais_agenda,
        "noticias": noticias,
    }
    FICHEIRO_DADOS.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("Feito: %d bandas, %d concertos, %d festivais, %d notícias, %d avisos." % (
        len(registos), len(concertos), len(festivais), len(noticias), len(avisos)))
    agenda = fontes["agenda"]
    print("Agenda: %s, %d linhas lidas, %d futuras, %d com bandas seguidas, %d quase correspondências, atualizada a %s." % (
        agenda["estado"], agenda["linhas"], agenda["futuras"], agenda.get("minhas", 0), len(agenda["quase"]), agenda["atualizada"]))
    for aviso in avisos:
        print("Aviso: " + aviso)
    return 0


if __name__ == "__main__":
    sys.exit(main())

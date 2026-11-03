#!/usr/bin/env python3
"""Testes das regras, sem rede: python3 scripts/test_atualizar.py"""
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar as a  # noqa: E402

HOJE = date(2026, 11, 3)


class Categorias(unittest.TestCase):
    def test_etiquetas(self):
        casos = {
            "thrash metal": "Thrash", "Death-Doom Metal": "Doom/Sludge", "deathcore": "Nu/Metalcore",
            "melodic death metal": "Death", "blackened death metal": "Death",
            "black metal": "Black", "atmospheric sludge metal": "Doom/Sludge",
            "nu metal": "Nu/Metalcore", "nu-metal": "Nu/Metalcore", "metalcore": "Nu/Metalcore",
            "heavy metal": None, "black music": None, "numetal band": None, "pop": None,
        }
        for etiqueta, esperado in casos.items():
            self.assertEqual(a.categoria_da_etiqueta(etiqueta), esperado, etiqueta)

    def test_votos_decidem(self):
        self.assertEqual(a.categoria_por_regras({"thrash metal": 10, "death metal": 2}, []), "Thrash")
        self.assertEqual(a.categoria_por_regras({"death metal": 2}, ["thrash metal"]), "Death")
        self.assertEqual(a.categoria_por_regras({}, ["doom metal"]), "Doom/Sludge")

    def test_etiqueta_com_poucos_votos_nao_decide(self):
        self.assertEqual(a.categoria_por_regras({"grunge": 68, "sludge metal": 3}, []), "Outros")
        self.assertEqual(a.categoria_por_regras({"djent": 9, "death metal": 3}, []), "Death")

    def test_grunge_e_progressivo_nao_tem_regras(self):
        self.assertEqual(a.categoria_por_regras({"grunge": 50}, []), "Outros")
        self.assertEqual(a.categoria_por_regras({"progressive metal": 50}, []), "Outros")
        self.assertIsNone(a.categoria_da_etiqueta("grunge"))
        self.assertIsNone(a.categoria_da_etiqueta("progressive metal"))

    def test_categorias_forcadas_do_ficheiro(self):
        avisos = []
        forcadas = a.carregar_categorias(avisos)
        self.assertEqual(avisos, [])
        self.assertEqual(forcadas, {
            "alice in chains": "Grunge", "nirvana": "Grunge", "tool": "Progressivo",
            "a perfect circle": "Progressivo", "ne obliviscaris": "Progressivo",
        })

    def test_sem_correspondencia_fica_em_outros(self):
        self.assertEqual(a.categoria_por_regras({"heavy metal": 5}, ["rock"]), "Outros")

    def test_nao_musica(self):
        self.assertTrue(a.nao_e_musica(["Audiobook"]))
        self.assertTrue(a.nao_e_musica(["spoken-word"]))
        self.assertFalse(a.nao_e_musica(["thrash metal"]))


class Lancamentos(unittest.TestCase):
    def test_filtra_e_ordena(self):
        grupos = [
            {"id": "1", "title": "Velho", "primary-type": "Album", "first-release-date": "2001-05-02"},
            {"id": "2", "title": "Novo", "primary-type": "EP", "first-release-date": "2026-10-01"},
            {"id": "3", "title": "Best Of", "primary-type": "Album", "secondary-types": ["Compilation"], "first-release-date": "2026-10-05"},
            {"id": "4", "title": "Sem data", "primary-type": "Album"},
            {"id": "5", "title": "Ao vivo", "primary-type": "Album", "secondary-types": ["Live"], "first-release-date": "2026-09"},
            {"id": "6", "title": "Parcial", "primary-type": "Album", "first-release-date": "2026-09"},
        ]
        r = a.converter_lancamentos("Banda", grupos)
        self.assertEqual([l["titulo"] for l in r], ["Novo", "Parcial", "Velho"])
        self.assertEqual([l["tipo"] for l in r], ["EP", "Álbum", "Álbum"])

    def test_recente(self):
        self.assertEqual(a.DIAS_RECENTES, 365)
        self.assertTrue(a.e_recente("2026-09-01", HOJE))
        self.assertTrue(a.e_recente("2026-10", HOJE))
        self.assertTrue(a.e_recente("2026-02-01", HOJE))
        self.assertTrue(a.e_recente("2025-11-03", HOJE))
        self.assertFalse(a.e_recente("2025-11-02", HOJE))
        self.assertFalse(a.e_recente("2026", HOJE))
        self.assertFalse(a.e_recente("2026-12-01", HOJE))

    def test_guarda_todos_os_do_ultimo_ano(self):
        hoje = a.agora().date()
        grupos = [{"id": str(i), "title": "T%d" % i, "primary-type": "Album",
                   "first-release-date": (hoje - timedelta(days=i + 1)).isoformat()} for i in range(14)]
        grupos += [{"id": "v%d" % i, "title": "V%d" % i, "primary-type": "Album",
                    "first-release-date": "19%02d-01-01" % (80 + i)} for i in range(5)]
        r = a.converter_lancamentos("B", grupos)
        self.assertEqual(len(r), 14)
        self.assertTrue(all(l["titulo"].startswith("T") for l in r))
        self.assertEqual(len(a.converter_lancamentos("B", grupos[14:])), 5)

    def test_le_todos_os_grupos_de_bandas_grandes(self):
        mb = a.MusicBrainz()
        paginas = [{"release-groups": [{"id": str(j)} for j in range(100)], "release-group-count": 1012}
                   for _ in range(10)] + [{"release-groups": [{"id": "x"}] * 12, "release-group-count": 1012}]
        with mock.patch.object(mb, "get", side_effect=paginas):
            self.assertEqual(len(mb.grupos_de_lancamentos("m")), 1012)

    def test_so_audiolivros(self):
        self.assertTrue(a.so_nao_musica([{"secondary-types": ["Audiobook"]}]))
        self.assertFalse(a.so_nao_musica([{"secondary-types": ["Audiobook"]}, {"secondary-types": []}]))
        self.assertFalse(a.so_nao_musica([]))


class Concertos(unittest.TestCase):
    def test_so_portugal_e_futuros(self):
        def ev(i, pais, quando, ofertas=None):
            return {"id": i, "datetime": quando, "url": "https://b/%s" % i, "offers": ofertas or [],
                    "venue": {"country": pais, "city": "C", "name": "S"}}
        r = a.filtrar_concertos([
            ev(1, "Spain", "2026-12-01T20:00:00"),
            ev(2, "Portugal", "2026-12-20T20:00:00"),
            ev(3, "Portugal", "2026-11-10T20:00:00", [{"url": "https://bilhetes/3"}]),
            ev(4, "Portugal", "2026-01-01T20:00:00"),
            ev(5, "PT", "2026-11-15T20:00:00"),
        ], HOJE)
        self.assertEqual([c["id"] for c in r], ["3", "5", "2"])
        self.assertEqual(r[0]["bilhetes"], "https://bilhetes/3")
        self.assertTrue(r[0]["tem_bilhetes"])
        self.assertEqual(r[1]["bilhetes"], "https://b/5")

    def test_resposta_de_erro_nao_rebenta(self):
        self.assertEqual(a.filtrar_concertos({"errorMessage": "x"}, HOJE), [])

    def test_codificacao_do_nome(self):
        self.assertEqual(a.nome_para_bandsintown("AC/DC"), "AC%252FDC")
        self.assertEqual(a.nome_para_bandsintown("Mötley"), "M%C3%B6tley")


class Feeds(unittest.TestCase):
    def test_rss(self):
        xml = b"""<rss><channel><title>Canal</title>
        <item><title><![CDATA[Novo &amp; <b>bom</b>]]></title><link>https://x/1</link>
          <pubDate>Mon, 02 Nov 2026 21:47:52 +0000</pubDate></item>
        <item><title>Sem data</title><link>https://x/2</link></item>
        <item><title>Script</title><link>javascript:alert(1)</link><pubDate>Mon, 02 Nov 2026 21:47:52 +0000</pubDate></item>
        </channel></rss>"""
        r = a.analisar_feed(xml, "F")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["titulo"], "Novo & bom")
        self.assertEqual(r[0]["data"].tzinfo, timezone.utc)

    def test_atom(self):
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
        <link rel="self" href="https://x/self"/><link href="https://x/a"/>
        <updated>2026-11-02T10:00:00Z</updated></entry></feed>"""
        self.assertEqual(a.analisar_feed(xml, "F")[0]["link"], "https://x/a")

    def test_rejeita_entidades(self):
        with self.assertRaises(ValueError):
            a.analisar_feed(b'<!DOCTYPE x [<!ENTITY a "b">]><rss></rss>', "F")


class Spotify(unittest.TestCase):
    def test_pagina_seguindo_next(self):
        paginas = [
            {"artists": {"items": [{"id": "1", "name": "A", "external_urls": {"spotify": "https://s/1"}}],
                         "next": "https://api.spotify.com/v1/me/following?after=1"}},
            {"artists": {"items": [{"id": "2", "name": "B", "genres": ["doom metal"]}], "next": None}},
        ]
        s = a.Spotify("i", "s", "r")
        s.acesso = "t"
        with mock.patch.object(a, "pedir", side_effect=[(200, json.dumps(p).encode()) for p in paginas]) as pedir:
            r = s.artistas_seguidos()
        self.assertEqual([x["nome"] for x in r], ["A", "B"])
        self.assertEqual(r[1]["generos_spotify"], ["doom metal"])
        self.assertIn("/me/following?type=artist&limit=50", pedir.call_args_list[0][0][0])

    def test_falha_usa_bands_txt(self):
        ambiente = {"SPOTIFY_CLIENT_ID": "i", "SPOTIFY_CLIENT_SECRET": "s", "SPOTIFY_REFRESH_TOKEN": "r"}
        avisos = []
        with mock.patch.dict(a.os.environ, ambiente), \
                mock.patch.object(a, "pedir", return_value=(400, b"{}")):
            bandas, origem, spotify = a.obter_bandas(avisos)
        self.assertEqual(origem, "bands.txt")
        self.assertIsNone(spotify)
        self.assertTrue(bandas)
        self.assertIn("bands.txt", avisos[0])

    AMBIENTE = {"SPOTIFY_CLIENT_ID": "i", "SPOTIFY_CLIENT_SECRET": "s", "SPOTIFY_REFRESH_TOKEN": "r"}
    GUARDADOS = {"fontes": {"bandas": "spotify"}, "bandas": [
        {"id": "a", "nome": "A", "spotify": "https://open.spotify.com/artist/abc1"},
        {"id": "b", "nome": "B", "spotify": "https://open.spotify.com/search/B"},
    ]}

    def bandas_com(self, anteriores, **pedir):
        avisos = []
        a.ESPERA_SPOTIFY["ate"] = None
        with mock.patch.dict(a.os.environ, self.AMBIENTE), \
                mock.patch.object(a, "carregar_dados_anteriores", return_value=anteriores), \
                mock.patch.object(a, "pedir", **pedir) as chamada:
            bandas, origem, spotify = a.obter_bandas(avisos)
        return bandas, origem, spotify, avisos, chamada

    def test_limite_de_pedidos_usa_a_lista_guardada(self):
        bandas, origem, spotify, avisos, _ = self.bandas_com(
            self.GUARDADOS, side_effect=a.ErroFonte("api.spotify.com pediu para esperar 81051 s", espera=81051))
        self.assertEqual(origem, "spotify_guardado")
        self.assertEqual([b["nome"] for b in bandas], ["A", "B"])
        self.assertEqual(bandas[0]["spotify_id"], "abc1")
        self.assertEqual(bandas[0]["spotify_url"], "https://open.spotify.com/artist/abc1")
        self.assertIsNone(bandas[1]["spotify_id"])
        self.assertIsNone(spotify)
        self.assertIn("última lista", avisos[0])
        self.assertGreater(a.ler_data(a.ESPERA_SPOTIFY["ate"]), a.agora())

    def test_sem_rede_usa_a_lista_guardada(self):
        _, origem, _, _, _ = self.bandas_com(self.GUARDADOS, side_effect=a.ErroFonte("sem resposta"))
        self.assertEqual(origem, "spotify_guardado")

    def test_durante_a_espera_nao_faz_pedidos(self):
        futuro = (a.agora() + timedelta(hours=5)).isoformat(timespec="seconds")
        anteriores = {"fontes": {"bandas": "spotify_guardado", "spotify_espera_ate": futuro}, "bandas": self.GUARDADOS["bandas"]}
        _, origem, _, _, chamada = self.bandas_com(anteriores, return_value=(200, b"{}"))
        self.assertEqual(origem, "spotify_guardado")
        chamada.assert_not_called()
        self.assertEqual(a.ler_data(a.ESPERA_SPOTIFY["ate"]), a.ler_data(futuro))

    def test_depois_da_espera_volta_a_pedir(self):
        passado = (a.agora() - timedelta(hours=1)).isoformat()
        anteriores = {"fontes": {"bandas": "spotify_guardado", "spotify_espera_ate": passado}, "bandas": self.GUARDADOS["bandas"]}
        token = (200, json.dumps({"access_token": "t"}).encode())
        pagina = (200, json.dumps({"artists": {"items": [{"id": "1", "name": "Nova"}], "next": None}}).encode())
        bandas, origem, spotify, _, _ = self.bandas_com(anteriores, side_effect=[token, pagina])
        self.assertEqual((origem, [b["nome"] for b in bandas]), ("spotify", ["Nova"]))
        self.assertIsNotNone(spotify)
        self.assertIsNone(a.ESPERA_SPOTIFY["ate"])

    def test_recusa_de_acesso_usa_o_plano_b(self):
        _, origem, _, avisos, _ = self.bandas_com(self.GUARDADOS, return_value=(400, b"{}"))
        self.assertEqual(origem, "bands.txt")
        self.assertIn("bands.txt", avisos[0])

    def test_lista_que_veio_do_plano_b_nao_e_guardada(self):
        anteriores = {"fontes": {"bandas": "bands.txt"}, "bandas": self.GUARDADOS["bandas"]}
        _, origem, _, _, _ = self.bandas_com(anteriores, side_effect=a.ErroFonte("sem resposta"))
        self.assertEqual(origem, "bands.txt")

    def test_nao_pede_o_que_ja_tem_link_de_album(self):
        s = mock.Mock()
        banda = {"lancamentos": [{"titulo": "T", "data": "2026-10-01", "capa": "c", "spotify": "https://open.spotify.com/album/1"}]}
        a.juntar_spotify(s, "x", banda, HOJE)
        s.albuns.assert_not_called()

    def test_junta_link_e_capa(self):
        s = mock.Mock()
        s.albuns.return_value = iter([{"name": "Título!", "images": [{"url": "g"}, {"url": "m"}],
                                       "external_urls": {"spotify": "https://s/a"}}])
        banda = {"lancamentos": [
            {"titulo": "titulo", "data": "2026-10-01", "capa": "c", "spotify": "p"}]}
        a.juntar_spotify(s, "x", banda, HOJE)
        self.assertEqual(banda["lancamentos"][0]["capa"], "m")
        self.assertEqual(banda["lancamentos"][0]["spotify"], "https://s/a")


MINHAS = {a.chave_comparacao(n): {"nome": n, "categoria": c} for n, c in [
    ("Moonspell", "Black"), ("Absu", "Thrash"), ("LIK", "Death"), ("1914", "Black"), ("Orthodox", "Nu/Metalcore"),
    ("Primordial", "Black"), ("Slugdge", "Death"), ("Mgła", "Black"), ("The Vintage Caravan", "Outros"),
    ("Dødheimsgard", "Black"), ("Irae", "Black"), ("Orbit Culture", "Death"), ("Soilwork", "Death"), ("Nu:n", "Outros"),
]}


class Agenda(unittest.TestCase):
    def linha(self, texto):
        return a.ler_linha_agenda(texto)

    def test_linha_simples_e_campos(self):
        r = self.linha("31/10/2026 – Moonspell + Nu:n – Hard Club (Porto)")
        self.assertEqual(r, {"data": "2026-10-31", "bandas": ["Moonspell", "Nu:n"], "sala": "Hard Club",
                             "cidade": "Porto", "evento": ""})

    def test_tracos_e_espacos_diferentes(self):
        for texto in ["31/10/2026 — Moonspell — Hard Club (Porto)", "31/10/2026 - Moonspell - Hard Club (Porto)",
                      "31/10/2026  –   Moonspell   –  Hard Club   ( Porto )  ", "31/10/2026–Moonspell–Hard Club (Porto)"]:
            r = self.linha(texto.strip())
            self.assertEqual((r["bandas"], r["sala"], r["cidade"]), (["Moonspell"], "Hard Club", "Porto"), texto)

    def test_hifen_dentro_de_nomes_nao_separa(self):
        r = self.linha("24/10/2026 – Cult Of Alcaeus – X-Treme Bar (Castelo Branco)")
        self.assertEqual((r["bandas"], r["sala"]), (["Cult Of Alcaeus"], "X-Treme Bar"))

    def test_separadores_de_bandas(self):
        self.assertEqual(self.linha("05/12/2026 – Moonspell w/ Orquestra Sinfonietta Lisboa – Multiusos (Alcobaça)")["bandas"],
                         ["Moonspell", "Orquestra Sinfonietta Lisboa"])
        self.assertEqual(self.linha("05/12/2026 – Um & Dois + Três – Sala (Porto)")["bandas"], ["Um", "Dois", "Três"])
        self.assertEqual(self.linha("19/11/2026 – Flotsam and Jetsam – RCA Club (Lisboa)")["bandas"], ["Flotsam and Jetsam"])

    def test_e_comercial_de_banda_seguida_fica(self):
        minhas = frozenset({a.chave_comparacao("Nick Cave & The Bad Seeds")})
        r = a.ler_linha_agenda("05/12/2026 – Nick Cave & The Bad Seeds + Outra – Sala (Porto)", minhas)
        self.assertEqual(r["bandas"], ["Nick Cave & The Bad Seeds", "Outra"])

    def test_evento_cidade_com_traco_e_sem_sala(self):
        r = self.linha("07/11/2026 – Banana Bong Fest – Idle Hand + Trasgo – CAAA Black Box (Guimarães)")
        self.assertEqual((r["evento"], r["bandas"], r["sala"], r["cidade"]),
                         ("Banana Bong Fest", ["Idle Hand", "Trasgo"], "CAAA Black Box", "Guimarães"))
        r = self.linha("21/11/2026 – Namek + Morto – Fashion Club (Santa Cruz – Madeira)")
        self.assertEqual((r["evento"], r["sala"], r["cidade"]), ("", "Fashion Club", "Santa Cruz – Madeira"))
        r = self.linha("31/10/2026 – Flavius Horrendus – (Chaves)")
        self.assertEqual((r["bandas"], r["sala"], r["cidade"]), (["Flavius Horrendus"], "", "Chaves"))

    def test_acentos(self):
        self.assertEqual(self.linha("11/12/2026 – Pé Roto + Mães Solteiras – Válega (Grijó)")["bandas"], ["Pé Roto", "Mães Solteiras"])

    def test_formato_diferente(self):
        for texto in ["2026-10-31 – Moonspell – Hard Club (Porto)", "31/10/2026 Moonspell no Hard Club",
                      "31/02/2026 – Moonspell – Hard Club (Porto)", "Outubro 2026", ""]:
            self.assertIsNone(self.linha(texto), texto)

    def test_pagina_html_real(self):
        pagina = """<html><head><style>p{}</style><script>var x="31/10/2026 – Falso – Falso (Falso)";</script></head>
        <body><p style="x">Atualizado a 22.10.2026</p><h2>Outubro 2026</h2>
        <p>31/10/2026 &#8211; Moonspell + <strong>Nu:n</strong> &#8211; Hard&nbsp;Club (Porto)<br />
        01/11/2026 – Eden´s Apple – Mouco (Porto)</p><p>31/10/2026 sem formato</p></body></html>"""
        lidos, rejeitadas, atualizada = a.analisar_agenda(pagina)
        self.assertEqual(atualizada, "2026-10-22")
        self.assertEqual([l["bandas"] for l in lidos], [["Moonspell", "Nu:n"], ["Eden´s Apple"]])
        self.assertEqual(lidos[0]["sala"], "Hard Club")
        self.assertEqual(rejeitadas, ["31/10/2026 sem formato"])

    def test_chave_de_comparacao(self):
        self.assertEqual(a.chave_comparacao("The Vintage Caravan"), "vintage caravan")
        self.assertEqual(a.chave_comparacao("Vintage Caravan"), "vintage caravan")
        self.assertEqual(a.chave_comparacao("Mgła"), "mgla")
        self.assertEqual(a.chave_comparacao("Dødheimsgard"), "dodheimsgard")
        self.assertEqual(a.chave_comparacao("Sněť"), "snet")
        self.assertEqual(a.chave_comparacao("The"), "the")
        self.assertEqual(a.chave_comparacao("Nu:n"), a.chave_comparacao("nu n"))

    def lido(self, *bandas):
        return {"data": "2026-12-01", "bandas": list(bandas), "sala": "S", "cidade": "Porto", "evento": ""}

    def test_correspondencia_por_nome_completo(self):
        for nome in ["Absurd", "Absu Tribute", "Liking", "Lik Lik", "1914 Tribute", "19141", "Orthodoxy", "Primordial Abyss",
                     "Slugdge Band", "Unorthodox", "Moonspells", "Vintage", "Caravan"]:
            self.assertIsNone(a.concerto_da_agenda(self.lido(nome), MINHAS), nome)
        for nome in ["Absu", "LIK", "lik", "1914", "ORTHODOX", "Primordial", "Slugdge", "Mgla", "Vintage Caravan",
                     "The Vintage Caravan", "Dodheimsgard", "Moonspell"]:
            self.assertIsNotNone(a.concerto_da_agenda(self.lido(nome), MINHAS), nome)

    def test_destaca_as_minhas_e_junta_categorias(self):
        r = a.concerto_da_agenda(self.lido("Amon Amarth", "Orbit Culture", "Irae"), MINHAS)
        self.assertEqual(r["bandas"], [{"nome": "Amon Amarth", "minha": False}, {"nome": "Orbit Culture", "minha": True},
                                       {"nome": "Irae", "minha": True}])
        self.assertEqual(r["categorias"], ["Death", "Black"])
        self.assertEqual(r["fontes"], ["Caminhos Metálicos"])
        self.assertEqual(r["link"], a.URL_AGENDA)
        self.assertFalse(r["tem_bilhetes"])

    def test_quase_correspondencias_nao_sao_aceites(self):
        lidos = [self.lido("Moonspel"), self.lido("Moonspell Orchestra"), self.lido("Slugde"), self.lido("Besta"), self.lido("Absu")]
        pares = {(p["agenda"], p["banda"]) for p in a.quase_correspondencias(lidos, MINHAS)}
        self.assertEqual(pares, {("Moonspel", "Moonspell"), ("Moonspell Orchestra", "Moonspell"), ("Slugde", "Slugdge")})
        self.assertIsNone(a.concerto_da_agenda(self.lido("Moonspel"), MINHAS))

    PAGINA = "<p>Atualizado a 22.10.2026</p><p>01/01/2020 – Moonspell – Velha (Porto)</p><p>01/12/2099 – Moonspell + Outra – Nova (Porto)</p><p>02/12/2099 – Outra – Nova (Porto)</p>"

    def agenda(self, resposta=None, erro=None, anteriores=()):
        avisos = []
        efeito = {"side_effect": erro} if erro else {"return_value": resposta}
        with mock.patch.object(a, "pedir", **efeito) as pedir:
            concertos, estado = a.obter_agenda(MINHAS, list(anteriores), HOJE, avisos)
        return concertos, estado, avisos, pedir

    def test_datas_passadas_ignoradas_e_uma_so_leitura(self):
        concertos, estado, avisos, pedir = self.agenda((200, self.PAGINA.encode()))
        self.assertEqual([c["data"] for c in concertos], ["2099-12-01"])
        self.assertEqual((estado["estado"], estado["linhas"], estado["futuras"], estado["minhas"], estado["atualizada"]),
                         ("ok", 3, 2, 1, "2026-10-22"))
        self.assertEqual(avisos, [])
        pedir.assert_called_once()
        self.assertEqual(pedir.call_args[1]["tentativas"], 1)

    ANTERIOR = [
        {"id": "x", "data": "2099-12-01", "bandas": [{"nome": "Moonspell", "minha": True}, {"nome": "Outra", "minha": False}],
         "categorias": ["Black"], "sala": "S", "cidade": "Porto", "evento": "", "fontes": ["Caminhos Metálicos"], "link": a.URL_AGENDA},
        {"id": "y", "data": "2020-01-01", "bandas": [{"nome": "Moonspell", "minha": True}], "categorias": ["Black"],
         "sala": "S", "cidade": "Porto", "evento": "", "fontes": ["Caminhos Metálicos"], "link": a.URL_AGENDA},
        {"id": "z", "data": "2099-12-02", "bandas": [{"nome": "Absu", "minha": True}], "categorias": ["Thrash"],
         "sala": "S", "cidade": "Porto", "evento": "", "fontes": ["Bandsintown"], "link": "https://b"},
    ]

    def test_falhas_mantem_os_guardados(self):
        casos = {
            "403": {"resposta": (403, b"")},
            "formato mudou": {"resposta": (200, b"<p>Nova pagina sem concertos</p>")},
            "pagina em falta": {"resposta": (404, b"")},
            "sem rede": {"erro": a.ErroFonte("sem resposta")},
            "erro inesperado": {"erro": RuntimeError("x")},
        }
        for nome, caso in casos.items():
            concertos, estado, avisos, _ = self.agenda(anteriores=self.ANTERIOR, **caso)
            self.assertEqual(estado["estado"], "indisponivel", nome)
            self.assertEqual([c["data"] for c in concertos], ["2099-12-01"], nome)
            self.assertEqual(concertos[0]["bandas"][1], {"nome": "Outra", "minha": False}, nome)
            self.assertEqual(len(avisos), 1, nome)

    def test_erros_nao_mostram_enderecos(self):
        _, estado, avisos, _ = self.agenda(erro=a.ErroFonte("x"))
        self.assertNotIn("http", avisos[0])

    def bit(self, data, cidade, banda, bilhetes=True, ident="b"):
        return {"id": ident, "data": data, "bandas": [{"nome": banda, "minha": True}], "categorias": ["Black"], "sala": "SB",
                "cidade": cidade, "evento": "", "fontes": ["Bandsintown"], "link": "https://bilhetes", "tem_bilhetes": bilhetes}

    def test_duplicados_entre_fontes(self):
        cm = a.concerto_da_agenda(dict(self.lido("Moonspell", "Outra"), cidade="Lisboa"), MINHAS)
        r = a.juntar_concertos([cm], [self.bit("2026-12-01T21:00:00", "Lisbon", "Moonspell")])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["fontes"], ["Caminhos Metálicos", "Bandsintown"])
        self.assertEqual(r[0]["link"], "https://bilhetes")
        self.assertTrue(r[0]["tem_bilhetes"])
        self.assertEqual(r[0]["data"], "2026-12-01T21:00:00")
        self.assertEqual(len(r[0]["bandas"]), 2)

    def test_nao_junta_o_que_e_diferente(self):
        cm = a.concerto_da_agenda(dict(self.lido("Moonspell"), cidade="Porto"), MINHAS)
        for outro in [self.bit("2026-12-02T21:00:00", "Porto", "Moonspell"), self.bit("2026-12-01T21:00:00", "Faro", "Moonspell"),
                      self.bit("2026-12-01T21:00:00", "Porto", "Irae")]:
            self.assertEqual(len(a.juntar_concertos([cm], [outro])), 2)

    def test_bandsintown_sem_agenda_e_ordem(self):
        registos = [
            {"nome": "A", "categoria": "Black", "concertos": [{"id": "1", "data": "2026-12-09T21:00:00", "cidade": "Porto", "sala": "S", "bilhetes": "https://b", "tem_bilhetes": True}]},
            {"nome": "B", "categoria": "Death", "concertos": [{"id": "1", "data": "2026-12-09T21:00:00", "cidade": "Porto", "sala": "S", "bilhetes": "https://b", "tem_bilhetes": True},
                                                               {"id": "2", "data": "2026-12-02T21:00:00", "cidade": "Faro", "sala": "T", "bilhetes": "https://c", "tem_bilhetes": False}]},
        ]
        r = a.juntar_concertos([], a.concertos_bandsintown(registos))
        self.assertEqual([c["data"][:10] for c in r], ["2026-12-02", "2026-12-09"])
        self.assertEqual([b["nome"] for b in r[1]["bandas"]], ["A", "B"])
        self.assertEqual(r[1]["categorias"], ["Black", "Death"])


class FluxoCompleto(unittest.TestCase):
    """Percorre processar_bandas com fontes falsas, como numa corrida com o Spotify ligado."""

    def correr(self, bandas, forcadas="{}"):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            (pasta / "categorias.json").write_text(forcadas, encoding="utf-8")
            recente = (a.agora().date() - timedelta(days=5)).isoformat()
            mb = mock.Mock()
            mb.id_por_spotify.side_effect = lambda i: (None, None) if i == "sem" else ("mb-" + i, "Group")
            mb.id_por_nome.return_value = None
            mb.etiquetas.side_effect = lambda m: {"thrash metal": 3} if m == "mb-t" else {}
            mb.grupos_de_lancamentos.side_effect = lambda m: {
                "mb-t": [{"id": "g", "title": "Novo", "primary-type": "Album", "first-release-date": recente}],
                "mb-livro": [{"id": "l", "title": "Livro", "primary-type": "Other", "secondary-types": ["Audiobook"], "first-release-date": "2020"}],
            }.get(m, [])
            spotify = mock.Mock()
            spotify.albuns.side_effect = lambda i, paginas=3: iter(
                [{"name": "Novo", "images": [{"url": "https://c"}], "external_urls": {"spotify": "https://s/album/1"}}]
                if i == "t" else [])
            with mock.patch.object(a, "MusicBrainz", return_value=mb), \
                    mock.patch.object(a, "FICHEIRO_CACHE", pasta / "cache.json"), \
                    mock.patch.object(a, "FICHEIRO_DADOS", pasta / "data.json"), \
                    mock.patch.object(a, "FICHEIRO_CATEGORIAS", pasta / "categorias.json"), \
                    mock.patch.dict(a.os.environ, {"BANDSINTOWN_APP_ID": ""}):
                registos, fontes = a.processar_bandas(bandas, spotify, [])
            cache = json.loads((pasta / "cache.json").read_text(encoding="utf-8"))
        return registos, fontes, cache

    @staticmethod
    def banda(nome, spotify_id, generos=None):
        return {"nome": nome, "spotify_id": spotify_id, "generos_spotify": generos or [],
                "spotify_url": "https://open.spotify.com/artist/" + spotify_id}

    def test_banda_com_lancamento_recente(self):
        registos, _, cache = self.correr([self.banda("Thrasher", "t")])
        self.assertEqual(registos[0]["categoria"], "Thrash")
        self.assertEqual(registos[0]["lancamentos"][0]["spotify"], "https://s/album/1")
        self.assertEqual(registos[0]["lancamentos"][0]["capa"], "https://c")
        self.assertEqual(cache["thrasher"]["mbid"], "mb-t")

    def test_reaproveita_capa_e_link_sem_pedir_ao_spotify(self):
        recente = (a.agora().date() - timedelta(days=5)).isoformat()
        anterior = {"bandas": [{"id": "thrasher", "lancamentos": [
            {"id": "g", "titulo": "Novo", "data": recente, "capa": "https://guardada", "spotify": "https://open.spotify.com/album/9"}]}]}
        with mock.patch.object(a, "carregar_dados_anteriores", return_value=anterior):
            registos, _, _ = self.correr([self.banda("Thrasher", "t")])
        l = registos[0]["lancamentos"][0]
        self.assertEqual((l["capa"], l["spotify"]), ("https://guardada", "https://open.spotify.com/album/9"))

    def test_exclui_o_que_nao_e_musica(self):
        registos, fontes, _ = self.correr([
            self.banda("Autor", "a", ["audiobook"]),
            self.banda("Livro", "livro"),
            self.banda("Sem Nada", "sem"),
            self.banda("Thrasher", "t"),
        ])
        self.assertEqual([r["nome"] for r in registos], ["Thrasher"])
        self.assertEqual(fontes["excluidas"], ["Autor", "Livro", "Sem Nada"])

    def test_etiqueta_tematica_nao_exclui_banda_com_discos(self):
        mb = mock.Mock()
        mb.id_por_spotify.return_value = ("mb-saor", "Group")
        mb.etiquetas.return_value = {"black metal": 5, "poetry": 2}
        mb.grupos_de_lancamentos.return_value = [
            {"id": "g", "title": "Roots", "primary-type": "Album", "first-release-date": "2013-05-16"}]
        spotify = mock.Mock()
        spotify.albuns.side_effect = lambda *x, **y: iter([])
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            (pasta / "categorias.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(a, "MusicBrainz", return_value=mb), \
                    mock.patch.object(a, "FICHEIRO_CACHE", pasta / "c.json"), \
                    mock.patch.object(a, "FICHEIRO_DADOS", pasta / "d.json"), \
                    mock.patch.object(a, "FICHEIRO_CATEGORIAS", pasta / "categorias.json"), \
                    mock.patch.dict(a.os.environ, {"BANDSINTOWN_APP_ID": ""}):
                registos, fontes = a.processar_bandas([self.banda("Saor", "s")], spotify, [])
        self.assertEqual([r["nome"] for r in registos], ["Saor"])
        self.assertEqual(fontes["excluidas"], [])

    def test_id_manual_tem_prioridade(self):
        mb = mock.Mock()
        mb.etiquetas.return_value = {"black metal": 1}
        manual = "AAAAAAAA-1111-2222-3333-444444444444".lower()
        cache = {"banda": {"mbid": "errado", "nome": "Banda", "etiquetas": {}, "verificado": HOJE.isoformat()}}
        r = a.entrada_mb(mb, cache, self.banda("Banda", "b"), HOJE, {"banda": manual})
        self.assertEqual(r["mbid"], manual)
        self.assertTrue(r["manual"])
        mb.id_por_spotify.assert_not_called()
        mb.id_por_nome.assert_not_called()
        # Já na cache: não volta a pedir as etiquetas.
        mb.etiquetas.reset_mock()
        a.entrada_mb(mb, cache, self.banda("Banda", "b"), HOJE, {"banda": manual})
        mb.etiquetas.assert_not_called()

    def test_id_manual_retirado_volta_a_pesquisar(self):
        mb = mock.Mock()
        mb.id_por_spotify.return_value = ("auto", "Group")
        mb.etiquetas.return_value = {}
        cache = {"banda": {"mbid": "m", "manual": True, "nome": "Banda", "etiquetas": {}, "verificado": HOJE.isoformat()}}
        r = a.entrada_mb(mb, cache, self.banda("Banda", "b"), HOJE, {})
        self.assertEqual(r["mbid"], "auto")
        self.assertNotIn("manual", r)

    def test_ficheiro_de_ids_manuais(self):
        with tempfile.TemporaryDirectory() as pasta:
            f = Path(pasta) / "m.json"
            f.write_text(json.dumps({"_nota": "x", "Mötley Crüe": "AAAAAAAA-1111-2222-3333-444444444444", "Má": "nao-e-id"}), encoding="utf-8")
            avisos = []
            with mock.patch.object(a, "FICHEIRO_MBID_MANUAL", f):
                r = a.carregar_mbid_manual(avisos)
        self.assertEqual(r, {"motley crue": "aaaaaaaa-1111-2222-3333-444444444444"})
        self.assertEqual(len(avisos), 1)
        self.assertEqual(a.carregar_mbid_manual([]), {})

    def test_pessoa_sem_discos_troca_pela_banda(self):
        mb = mock.Mock()
        mb.id_por_spotify.return_value = ("pessoa", "Person")
        mb.id_por_nome.return_value = "banda"
        mb.grupos_de_lancamentos.return_value = [
            {"id": "x", "title": "Ao vivo", "primary-type": "Album", "secondary-types": ["Live"], "first-release-date": "1997"}]
        mb.etiquetas.return_value = {}
        r = a.entrada_mb(mb, {}, self.banda("Marilyn Manson", "m"), HOJE)
        self.assertEqual(r["mbid"], "banda")
        mb.id_por_nome.assert_called_once_with("Marilyn Manson", so_grupos=True)

    def test_artista_a_solo_com_discos_fica(self):
        mb = mock.Mock()
        mb.id_por_spotify.return_value = ("ozzy", "Person")
        mb.grupos_de_lancamentos.return_value = [
            {"id": "x", "title": "Ordinary Man", "primary-type": "Album", "first-release-date": "2020-02-21"}]
        mb.etiquetas.return_value = {}
        r = a.entrada_mb(mb, {}, self.banda("Ozzy Osbourne", "o"), HOJE)
        self.assertEqual(r["mbid"], "ozzy")
        mb.id_por_nome.assert_not_called()

    def test_categoria_forcada_tem_prioridade(self):
        registos, fontes, _ = self.correr(
            [self.banda("Thrasher", "t"), self.banda("Autor", "a", ["audiobook"]), self.banda("Fora", "t")],
            '{"thrasher": "Black", "AUTOR": "Outros", "Fora": "Ignorar"}')
        self.assertEqual({r["nome"]: r["categoria"] for r in registos}, {"Thrasher": "Black", "Autor": "Outros"})
        self.assertEqual(fontes["excluidas"], [])


if __name__ == "__main__":
    unittest.main()

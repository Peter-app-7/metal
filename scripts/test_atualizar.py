#!/usr/bin/env python3
"""Testes das regras, sem rede: python3 scripts/test_atualizar.py"""
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atualizar as a  # noqa: E402

a.FICHEIRO_FESTIVAIS_REAL = a.FICHEIRO_FESTIVAIS

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
        # Sem dados guardados: o teste não pode depender do data.json real nem da espera lá registada.
        with mock.patch.dict(a.os.environ, ambiente), \
                mock.patch.object(a, "carregar_dados_anteriores", return_value={}), \
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

    def test_concertos_repetidos_na_agenda(self):
        um = a.concerto_da_agenda(dict(self.lido("Moonspell", "Outra"), cidade="Lisboa"), MINHAS)
        dois = a.concerto_da_agenda(dict(self.lido("Moonspell", "Irae"), cidade="lisboa"), MINHAS)
        antes = a.concerto_da_agenda(dict(self.lido("Absu"), data="2026-11-20"), MINHAS)
        r = a.concertos_sem_repetidos([um, dois, antes])
        self.assertEqual([c["data"] for c in r], ["2026-11-20", "2026-12-01"])
        self.assertEqual([b["nome"] for b in r[1]["bandas"]], ["Moonspell", "Outra"])
        self.assertEqual(r[1]["categorias"], ["Black"])
        self.assertEqual(r[1]["fontes"], ["Caminhos Metálicos"])

    def test_nao_junta_o_que_e_diferente(self):
        base = a.concerto_da_agenda(dict(self.lido("Moonspell"), cidade="Porto"), MINHAS)
        for outro in [dict(self.lido("Moonspell"), cidade="Porto", data="2026-12-02"), dict(self.lido("Moonspell"), cidade="Faro"),
                      dict(self.lido("Irae"), cidade="Porto")]:
            self.assertEqual(len(a.concertos_sem_repetidos([base, a.concerto_da_agenda(outro, MINHAS)])), 2)


class Festivais(unittest.TestCase):
    AGENDA = """<p>Atualizado a 22.10.2026</p>
    <p>07/11/2026 – Banana Bong Fest – Idle Hand + Irae + Trasgo – CAAA Black Box (Guimarães)</p>
    <p>06/11/2026 – Viseu Death Fest – Orfeão (Viseu)</p><p>07/11/2026 – Viseu Death Fest – Orfeão (Viseu)</p>
    <p>03/12/2026 – Invicta Requiem Mass IX – Válega (Válega – Ovar)</p><p>04/12/2026 – Invicta Requiem Mass IX – Válega (Válega – Ovar)</p>
    <p>05/12/2026 – Invicta Requiem Mass IX – Válega (Válega – Ovar)</p>
    <p>26/12/2026 – Assembleia do Metal – (Pindelo dos Milagres)</p>
    <p>19/12/2026 – Laurus Nobilis Xmas Gathering – Casa Artista Amador (Famalicão)</p>
    <p>28/11/2026 – Erudica – Okkulto Bar (Braga)</p><p>30/11/2026 – Erudica + Viledog – Buraco (Ovar)</p>
    <p>19/11/2026 – Flotsam and Jetsam – RCA Club (Lisboa)</p>
    <p>01/12/2026 – Manifest – Sala (Porto)</p><p>02/12/2026 – Infest + Festa Brava – Sala (Porto)</p>
    <p>18/12/2026 – Thrashin the Hills – Speedemon + Ural – UDCA (Vila Franca)</p>
    <p>30/10/2026 – Patrimónios de Peso – Expocoa (Foz Côa)</p><p>31/10/2026 – Patrimónios de Peso – Expocoa (Foz Côa)</p>
    <p>01/11/2026 – Fest Passado – Sala (Porto)</p>"""

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.ficheiro = Path(self.pasta.name) / "festivais.json"
        registo = mock.patch.object(a, "FICHEIRO_FESTIVAIS", self.ficheiro)
        registo.start()
        self.addCleanup(registo.stop)

    def lidos(self, pagina=None):
        return a.analisar_agenda(pagina or self.AGENDA)[0]

    def obter(self, lidos=(), ok=True, guardados=None):
        avisos = []
        festivais, agenda = a.obter_festivais(list(lidos), ok, guardados, MINHAS, HOJE, avisos)
        return festivais, agenda, avisos

    def manual(self, *entradas):
        self.ficheiro.write_text(json.dumps({"_instrucoes": "x", "_exemplo": {"nome": "Não"}, "festivais": list(entradas)}), encoding="utf-8")

    def test_palavras_de_festival(self):
        for nome in ["Viseu Death Fest", "UV Almost Fest IV", "Moonfest", "Festival de Metal", "Portugal Open Air", "Open-Air X",
                     "Xmas Gathering", "Metal Weekend", "Assembleia do Metal", "HEAVY FEST"]:
            self.assertTrue(a.e_nome_de_festival(nome), nome)
        for nome in ["Manifest", "Infest", "Festa Brava", "Erudica", "Fester", "Festering Wounds", "Weekender"]:
            self.assertFalse(a.e_nome_de_festival(nome), nome)

    def test_agenda_com_festivais(self):
        festivais, agenda, avisos = self.obter(self.lidos())
        self.assertEqual([(f["nome"], f["data_inicio"], f["data_fim"]) for f in festivais], [
            ("Viseu Death Fest", "2026-11-06", "2026-11-07"),
            ("Banana Bong Fest", "2026-11-07", "2026-11-07"),
            ("Invicta Requiem Mass IX", "2026-12-03", "2026-12-05"),
            ("Laurus Nobilis Xmas Gathering", "2026-12-19", "2026-12-19"),
            ("Assembleia do Metal", "2026-12-26", "2026-12-26"),
        ])
        self.assertEqual(avisos, [])
        self.assertEqual(festivais[2]["cidade"], "Válega – Ovar")
        self.assertEqual(festivais[4]["local"], "")
        self.assertEqual(festivais[0]["link"], a.URL_AGENDA)
        self.assertEqual(festivais[0]["fontes"], ["Caminhos Metálicos"])

    def test_banda_sozinha_e_passados_nao_entram(self):
        nomes = [f["nome"] for f in self.obter(self.lidos())[0]]
        for nome in ["Erudica", "Flotsam and Jetsam", "Manifest", "Infest", "Thrashin the Hills", "Patrimónios de Peso", "Fest Passado"]:
            self.assertNotIn(nome, nomes)

    def test_banda_seguida_em_dias_seguidos_nao_e_festival(self):
        lidos = self.lidos("<p>01/12/2026 – Irae – Sala (Porto)</p><p>02/12/2026 – Irae – Sala (Porto)</p>")
        self.assertEqual(self.obter(lidos)[0], [])

    def test_etiqueta_tens_bandas(self):
        festivais = self.obter(self.lidos())[0]
        banana = next(f for f in festivais if f["nome"] == "Banana Bong Fest")
        self.assertTrue(banana["tem_minhas"])
        self.assertEqual([b["nome"] for b in banana["bandas"] if b["minha"]], ["Irae"])
        viseu = next(f for f in festivais if f["nome"] == "Viseu Death Fest")
        self.assertFalse(viseu["tem_minhas"])
        self.assertEqual(viseu["bandas"], [])

    def test_nome_curto_no_cartaz_sem_falso_positivo(self):
        self.manual({"nome": "F", "data_inicio": "2026-12-01", "bandas": ["Absurd", "Liking", "Orthodoxy"]})
        self.assertFalse(self.obter()[0][0]["tem_minhas"])

    def test_agenda_sem_festivais(self):
        lidos = self.lidos("<p>28/11/2026 – Erudica – Okkulto Bar (Braga)</p>")
        festivais, agenda, _ = self.obter(lidos)
        self.assertEqual((festivais, agenda), ([], []))

    def test_agenda_indisponivel_usa_guardados_e_manual(self):
        guardados = self.obter(self.lidos())[1]
        self.manual({"nome": "Manual Fest", "data_inicio": "2026-11-20", "cidade": "Faro"})
        festivais, agenda, _ = self.obter(ok=False, guardados=guardados)
        self.assertIn("Manual Fest", [f["nome"] for f in festivais])
        self.assertIn("Viseu Death Fest", [f["nome"] for f in festivais])
        self.assertEqual(len(agenda), len(guardados))
        self.assertEqual(len(festivais), len(guardados) + 1)

    def test_agenda_indisponivel_sem_guardados(self):
        self.manual({"nome": "Manual Fest", "data_inicio": "2026-11-20"})
        self.assertEqual([f["nome"] for f in self.obter(ok=False)[0]], ["Manual Fest"])
        self.manual()
        self.assertEqual(self.obter(ok=False)[0], [])

    def test_guardado_que_ja_passou_sai(self):
        guardado = [a.novo_festival("Velho Fest", "2026-01-01", "2026-01-02", "", "", "", [], a.FONTE_AGENDA, MINHAS)]
        self.assertEqual(self.obter(ok=False, guardados=guardado)[:2], ([], []))

    def test_manual_vazio_ou_em_falta(self):
        self.assertEqual(self.obter()[0], [])
        self.manual()
        self.assertEqual(self.obter()[0], [])
        self.ficheiro.write_text("[]", encoding="utf-8")
        self.assertEqual(self.obter()[0], [])

    def test_manual_com_entradas(self):
        self.manual({"nome": "SWR Fest", "data_inicio": "2027-04-23", "data_fim": "2027-04-25", "local": "Barroselas", "cidade": "Viana",
                     "link": "https://exemplo.pt/swr", "bandas": ["Irae", "Outra"]},
                    {"nome": "Um Dia", "data_inicio": "2026-12-01"})
        festivais, agenda, avisos = self.obter()
        self.assertEqual([f["nome"] for f in festivais], ["Um Dia", "SWR Fest"])
        self.assertEqual(festivais[0]["data_fim"], "2026-12-01")
        self.assertEqual(festivais[1]["bandas"], [{"nome": "Irae", "minha": True}, {"nome": "Outra", "minha": False}])
        self.assertTrue(festivais[1]["tem_minhas"])
        self.assertEqual(festivais[1]["fontes"], ["Manual"])
        self.assertEqual((agenda, avisos), ([], []))

    def test_exemplo_nao_aparece_como_festival(self):
        real = json.loads(a.FICHEIRO_FESTIVAIS_REAL.read_text(encoding="utf-8"))
        self.ficheiro.write_text(json.dumps({"_instrucoes": real["_instrucoes"], "_exemplo": real["_exemplo"], "festivais": []}), encoding="utf-8")
        self.assertEqual(self.obter(), ([], [], []))

    def real(self):
        self.ficheiro.write_text(a.FICHEIRO_FESTIVAIS_REAL.read_text(encoding="utf-8"), encoding="utf-8")

    def test_ficheiro_real_e_valido_e_sem_exemplo(self):
        self.real()
        festivais, _, avisos = self.obter()
        self.assertEqual(avisos, [])
        self.assertNotIn("Nome do Festival", [f["nome"] for f in festivais])
        self.assertEqual(len(festivais), 4)
        for festival in festivais:
            self.assertTrue(festival["local"] and festival["cidade"] and festival["bandas"], festival["nome"])

    def test_quatro_manuais_nao_repetem_se_a_agenda_os_incluir(self):
        self.real()
        # As mesmas datas, como festivais da agenda, com variações reais de escrita.
        pagina = """<p>07/11/2026 – Rock da Velha Fest – Mães Solteiras + Avesso + Klatter – Musica Velha (Pernes)</p>
        <p>18/12/2026 – Thrashin' The Hills Festival – Speedemon + Ural – UDCA (Á dos Loucos – VFX)</p>
        <p>19/12/2026 – Terapia Natalícia Fest – Serrabulho + Jarda – Woodstock 69 (Porto)</p>
        <p>26/12/2026 – Ultimate Mosh Of The Year Weekend – Holocausto Canibal + Crucivore – Ferro Bar (Porto)</p>"""
        festivais = self.obter(self.lidos(pagina))[0]
        self.assertEqual([f["nome"] for f in festivais],
                         ["Rock da Velha", "Thrashin the Hills", "Terapia Natalicia", "Ultimate Mosh Of The Year"])
        for festival in festivais:
            self.assertEqual(festival["fontes"], ["Manual", "Caminhos Metálicos"], festival["nome"])

    def test_quatro_manuais_com_agenda_real_e_indisponivel(self):
        self.real()
        guardados = self.obter(self.lidos())[1]
        for festivais in (self.obter(self.lidos())[0], self.obter(ok=False, guardados=guardados)[0]):
            nomes = [a.chave_comparacao(f["nome"]) for f in festivais]
            self.assertEqual(len(nomes), len(set(nomes)))
            self.assertEqual(len(nomes), 9)  # 5 da agenda de teste + 4 manuais

    def test_festival_repetido_nas_duas_fontes(self):
        self.manual({"nome": "Invicta Requiem Mass", "data_inicio": "2026-12-03", "data_fim": "2026-12-06", "local": "Pavilhão",
                     "cidade": "Ovar", "link": "https://exemplo.pt/irm", "bandas": ["Irae"]},
                    {"nome": "viseu death fest", "data_inicio": "2026-11-06", "data_fim": "2026-11-07"})
        festivais = self.obter(self.lidos())[0]
        nomes = [f["nome"] for f in festivais]
        self.assertEqual(len(festivais), 5)
        self.assertEqual(nomes.count("Invicta Requiem Mass IX"), 0)
        invicta = next(f for f in festivais if f["nome"] == "Invicta Requiem Mass")
        self.assertEqual((invicta["local"], invicta["cidade"], invicta["link"]), ("Pavilhão", "Ovar", "https://exemplo.pt/irm"))
        self.assertEqual((invicta["data_inicio"], invicta["data_fim"]), ("2026-12-03", "2026-12-06"))
        self.assertEqual(invicta["fontes"], ["Manual", "Caminhos Metálicos"])
        self.assertTrue(invicta["tem_minhas"])
        viseu = next(f for f in festivais if a.chave_comparacao(f["nome"]) == "viseu death fest")
        self.assertEqual((viseu["local"], viseu["cidade"], viseu["link"]), ("Orfeão", "Viseu", a.URL_AGENDA))

    def test_mesmo_nome_noutra_data_nao_junta(self):
        self.manual({"nome": "Viseu Death Fest", "data_inicio": "2027-11-05"})
        self.assertEqual(sum(f["nome"] == "Viseu Death Fest" for f in self.obter(self.lidos())[0]), 2)

    def test_entradas_invalidas_nao_rebentam(self):
        self.manual({"nome": "Sem Data"}, {"nome": "Má", "data_inicio": "31/12/2026"}, {"data_inicio": "2026-12-01"},
                    {"nome": "Fim Antes", "data_inicio": "2026-12-02", "data_fim": "2026-12-01"}, "texto",
                    {"nome": "Link Mau", "data_inicio": "2026-12-01", "link": "javascript:alert(1)"})
        festivais, _, avisos = self.obter()
        self.assertEqual([(f["nome"], f["link"]) for f in festivais], [("Link Mau", "")])
        self.assertEqual(len(avisos), 6)
        self.ficheiro.write_text("{ erro", encoding="utf-8")
        self.assertEqual(len(self.obter()[2]), 1)

    def test_corrida_completa_escreve_e_mantem_na_falha(self):
        with tempfile.TemporaryDirectory() as pasta:
            dados = Path(pasta) / "data.json"
            registos = [{"id": "irae", "nome": "Irae", "categoria": "Black", "spotify": "https://x", "lancamentos": []}]
            def correr(resposta):
                def pedir(url, *x, **y):
                    if url == a.URL_AGENDA:
                        return resposta
                    raise a.ErroFonte("sem rede")
                avisos_saida = io.StringIO()
                with mock.patch.object(a, "obter_bandas", return_value=([{"nome": "Irae"}], "spotify_guardado", None)), \
                        mock.patch.object(a, "processar_bandas", return_value=(registos, {"excluidas": []})), \
                        mock.patch.object(a, "FICHEIRO_DADOS", dados), mock.patch.object(a, "pedir", side_effect=pedir), \
                        mock.patch("sys.stdout", avisos_saida):
                    self.assertEqual(a.main(), 0)
                return json.loads(dados.read_text(encoding="utf-8"))
            pagina = "<p>01/12/2099 – Grande Fest – Irae + Outra – Sala (Porto)</p>".encode()
            r = correr((200, pagina))
            self.assertEqual([f["nome"] for f in r["festivais"]], ["Grande Fest"])
            self.assertEqual(r["festivais"], r["festivais_agenda"])
            self.assertTrue(r["festivais"][0]["tem_minhas"])
            r = correr((403, b""))
            self.assertEqual(r["fontes"]["agenda"]["estado"], "indisponivel")
            self.assertEqual([f["nome"] for f in r["festivais"]], ["Grande Fest"])
            self.assertEqual(len(r["concertos"]), 1)


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
                    mock.patch.object(a, "FICHEIRO_CATEGORIAS", pasta / "categorias.json"):
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
                    mock.patch.object(a, "FICHEIRO_CATEGORIAS", pasta / "categorias.json"):
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

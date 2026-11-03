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
        self.assertTrue(a.e_recente("2026-09-01", HOJE))
        self.assertTrue(a.e_recente("2026-10", HOJE))
        self.assertFalse(a.e_recente("2026", HOJE))
        self.assertFalse(a.e_recente("2026-07-01", HOJE))
        self.assertFalse(a.e_recente("2026-12-01", HOJE))

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

    def test_junta_link_e_capa(self):
        s = mock.Mock()
        s.albuns.return_value = iter([{"name": "Título!", "images": [{"url": "g"}, {"url": "m"}],
                                       "external_urls": {"spotify": "https://s/a"}}])
        banda = {"lancamentos": [
            {"titulo": "titulo", "data": "2026-10-01", "capa": "c", "spotify": "p"}]}
        a.juntar_spotify(s, "x", banda, HOJE)
        self.assertEqual(banda["lancamentos"][0]["capa"], "m")
        self.assertEqual(banda["lancamentos"][0]["spotify"], "https://s/a")


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

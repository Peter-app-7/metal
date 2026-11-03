// app.js: lê o data.json e desenha os ecrãs. A página não fala com mais nenhum serviço.
// Tudo o que vem de fora entra como texto (nunca como HTML) e os links são verificados.
'use strict';

const CATEGORIAS = ['Thrash', 'Death', 'Black', 'Doom/Sludge', 'Nu/Metalcore', 'Progressivo', 'Grunge', 'Outros'];
const FILTROS = ['Todas', ...CATEGORIAS];
const DIAS_RECENTES = 365;
const LANCAMENTOS_POR_PAGINA = 30;
const HORAS_ANTIGO = 24;
const MILISSEGUNDOS_HORA = 60 * 60 * 1000;

const conteudo = document.getElementById('conteudo');
const atualizado = document.getElementById('atualizado');

let dados = null;
let erroCarregar = false;
let carregadoEm = 0;
let filtro = lerFiltro();
let lancamentosVisiveis = LANCAMENTOS_POR_PAGINA;
let scrollDaLista = 0;

function lerFiltro() {
  try {
    const guardado = localStorage.getItem('metal-filtro');
    return FILTROS.includes(guardado) ? guardado : 'Todas';
  } catch (erro) {
    return 'Todas';
  }
}

function guardarFiltro() {
  try {
    localStorage.setItem('metal-filtro', filtro);
  } catch (erro) {
    // Sem armazenamento (navegação privada): o filtro vale só para esta visita.
  }
}

// Cria um elemento. Os filhos de texto nunca são interpretados como HTML.
function el(etiqueta, atributos, ...filhos) {
  const elemento = document.createElement(etiqueta);
  for (const [nome, valor] of Object.entries(atributos || {})) {
    if (nome === 'aoTocar') elemento.addEventListener('click', valor);
    else if (valor !== null && valor !== undefined) elemento.setAttribute(nome, valor);
  }
  for (const filho of filhos) {
    if (filho !== null && filho !== undefined) elemento.append(filho);
  }
  return elemento;
}

// Só deixa passar endereços http(s). Devolve null para o resto.
function linkSeguro(endereco) {
  try {
    const url = new URL(endereco);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch (erro) {
    return null;
  }
}

// Uma linha que abre fora da app. Sem link válido, fica só o texto.
function linhaLink(endereco, ...filhos) {
  const href = linkSeguro(endereco);
  if (!href) return el('div', { class: 'linha' }, ...filhos);
  return el('a', { class: 'linha', href, target: '_blank', rel: 'noopener noreferrer' }, ...filhos);
}

// --- Datas ---------------------------------------------------------------------

// Nomes escritos aqui: nem todos os aparelhos formatam igual os meses abreviados.
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
const DIAS_SEMANA = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];

function diaMes(data) {
  return `${data.getDate()} ${MESES[data.getMonth()]}`;
}

function hora(data) {
  return `${String(data.getHours()).padStart(2, '0')}:${String(data.getMinutes()).padStart(2, '0')}`;
}

// As datas do MusicBrainz podem ter só ano, ou ano e mês.
function dataLancamento(texto) {
  const [ano, mes, dia] = String(texto || '').split('-').map(Number);
  if (!ano) return { data: null, texto: '' };
  if (!mes) return { data: null, texto: String(ano) };
  if (!dia) return { data: new Date(ano, mes - 1, 1), texto: `${MESES[mes - 1]} ${ano}` };
  const data = new Date(ano, mes - 1, dia);
  return { data, texto: `${diaMes(data)} ${ano}` };
}

function eRecente(lancamento) {
  const { data } = dataLancamento(lancamento.data);
  if (!data) return false;
  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);
  const limite = new Date(hoje);
  limite.setDate(limite.getDate() - DIAS_RECENTES);
  return data >= limite && data <= hoje;
}

function tempoPassado(data) {
  const minutos = Math.floor((Date.now() - data.getTime()) / 60000);
  if (minutos < 1) return 'agora mesmo';
  if (minutos < 60) return `há ${minutos} min`;
  const horas = Math.floor(minutos / 60);
  if (horas < 24) return `há ${horas} h`;
  const dias = Math.floor(horas / 24);
  return dias === 1 ? 'há 1 dia' : `há ${dias} dias`;
}

function desenharAtualizado() {
  const data = dados ? new Date(dados.atualizado) : null;
  if (!data || Number.isNaN(data.getTime())) {
    atualizado.textContent = erroCarregar ? 'Sem dados. Verifica a internet.' : 'A carregar…';
    return;
  }
  const quando = `${diaMes(data)}, ${hora(data)}`;
  let texto = `Atualizado ${tempoPassado(data)} · ${quando}`;
  if (erroCarregar) texto += ' · sem internet';
  atualizado.textContent = texto;
  atualizado.dataset.antigo = String(Date.now() - data.getTime() > HORAS_ANTIGO * MILISSEGUNDOS_HORA);
}

// --- Peças dos ecrãs ------------------------------------------------------------

function capa(endereco) {
  const caixa = el('span', { class: 'capa', 'aria-hidden': 'true' }, '♪');
  const href = linkSeguro(endereco);
  if (href) {
    // A imagem fica por cima da nota musical. Se não existir capa, sai e fica a nota.
    const imagem = el('img', { src: href, alt: '', loading: 'lazy', decoding: 'async' });
    imagem.addEventListener('error', () => imagem.remove());
    caixa.append(imagem);
  }
  return caixa;
}

function linhaLancamento(lancamento, nomeBanda) {
  const detalhes = [nomeBanda, lancamento.tipo, dataLancamento(lancamento.data).texto].filter(Boolean);
  return linhaLink(
    lancamento.spotify,
    capa(lancamento.capa),
    el('span', { class: 'linha-texto' },
      el('span', { class: 'linha-titulo' }, lancamento.titulo),
      el('span', { class: 'linha-detalhe' }, detalhes.join(' · '))),
    el('span', { class: 'seta', 'aria-hidden': 'true' }, '↗'),
  );
}

function linhaConcerto(concerto, nomesBandas) {
  const data = new Date(concerto.data);
  const valida = !Number.isNaN(data.getTime());
  const local = [concerto.sala, concerto.cidade].filter(Boolean).join(' · ');
  const ano = valida && data.getFullYear() !== new Date().getFullYear() ? ` ${data.getFullYear()}` : '';
  const quando = valida ? `${DIAS_SEMANA[data.getDay()]}${ano} · ${hora(data)}` : '';
  return linhaLink(
    concerto.bilhetes,
    el('span', { class: 'dia' },
      el('span', { class: 'dia-numero' }, valida ? String(data.getDate()) : '?'),
      el('span', { class: 'dia-mes' }, valida ? MESES[data.getMonth()] : '')),
    el('span', { class: 'linha-texto' },
      el('span', { class: 'linha-titulo' }, nomesBandas),
      el('span', { class: 'linha-detalhe' }, local),
      el('span', { class: 'linha-detalhe' }, quando)),
    el('span', { class: 'etiqueta-bilhetes' }, concerto.tem_bilhetes ? 'Bilhetes ↗' : 'Ver ↗'),
  );
}

function mensagemSemConcertos() {
  const estado = dados.fontes && dados.fontes.concertos;
  if (estado === 'sem_app_id') {
    return 'Ainda não há concertos para mostrar: falta a chave (app_id) da Bandsintown. Quando estiver ligada, os próximos concertos em Portugal aparecem aqui.';
  }
  if (estado === 'recusado') return 'Não há concertos para mostrar: a Bandsintown recusou a chave (app_id).';
  return filtro === 'Todas' ? 'Não há concertos marcados em Portugal.' : 'Sem concertos marcados em Portugal nesta categoria.';
}

// --- Ecrã Bandas ----------------------------------------------------------------

function bandasFiltradas() {
  return filtro === 'Todas' ? dados.bandas : dados.bandas.filter((banda) => banda.categoria === filtro);
}

function desenharFiltros() {
  const caixa = el('div', { class: 'filtros', role: 'group', 'aria-label': 'Filtrar por categoria' });
  for (const nome of FILTROS) {
    caixa.append(el('button', {
      type: 'button',
      class: 'filtro',
      'aria-pressed': String(nome === filtro),
      aoTocar: () => {
        filtro = nome;
        lancamentosVisiveis = LANCAMENTOS_POR_PAGINA;
        guardarFiltro();
        desenhar();
      },
    }, nome));
  }
  return caixa;
}

function desenharLancamentos(bandas) {
  const recentes = [];
  for (const banda of bandas) {
    for (const lancamento of banda.lancamentos) {
      if (eRecente(lancamento)) recentes.push({ lancamento, banda });
    }
  }
  recentes.sort((a, b) => (a.lancamento.data < b.lancamento.data ? 1 : -1));

  const seccao = el('section', null, el('h2', null, 'Novos lançamentos (último ano)'));
  if (!recentes.length) {
    seccao.append(el('p', { class: 'vazio' }, 'Sem lançamentos do último ano nesta categoria.'));
    return seccao;
  }
  const visiveis = recentes.slice(0, lancamentosVisiveis);
  const lista = el('ul', { class: 'lista' });
  for (const { lancamento, banda } of visiveis) {
    lista.append(el('li', null, linhaLancamento(lancamento, banda.nome)));
  }
  seccao.append(lista);
  if (visiveis.length < recentes.length) {
    seccao.append(el('button', {
      type: 'button',
      class: 'botao-secundario',
      aoTocar: () => {
        lancamentosVisiveis += LANCAMENTOS_POR_PAGINA;
        desenhar();
      },
    }, 'Mostrar mais'));
  }
  return seccao;
}

function contagemDaBanda(banda) {
  const novos = banda.lancamentos.filter(eRecente).length;
  const partes = [];
  if (novos) partes.push(novos === 1 ? '1 novo' : `${novos} novos`);
  return partes.join(' · ');
}

function desenharListaBandas(bandas) {
  const seccao = el('section', null, el('h2', null, `Todas as bandas (${bandas.length})`));
  if (!bandas.length) {
    seccao.append(el('p', { class: 'vazio' }, 'Sem bandas nesta categoria.'));
    return seccao;
  }
  for (const categoria of CATEGORIAS) {
    const grupo = bandas
      .filter((banda) => banda.categoria === categoria)
      .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-PT', { sensitivity: 'base' }));
    if (!grupo.length) continue;
    seccao.append(el('h3', null, `${categoria} (${grupo.length})`));
    const lista = el('ul', { class: 'lista' });
    for (const banda of grupo) {
      const contagem = contagemDaBanda(banda);
      lista.append(el('li', null, el('button', {
        type: 'button',
        class: 'linha',
        aoTocar: () => {
          scrollDaLista = window.scrollY;
          location.hash = `#banda/${encodeURIComponent(banda.id)}`;
        },
      },
      el('span', { class: 'linha-texto linha-titulo' }, banda.nome),
      contagem ? el('span', { class: 'contador' }, contagem) : null,
      el('span', { class: 'seta', 'aria-hidden': 'true' }, '›'))));
    }
    seccao.append(lista);
  }
  return seccao;
}

function desenharBandas() {
  const bandas = bandasFiltradas();
  const origem = dados.fontes && dados.fontes.bandas;
  conteudo.replaceChildren(
    el('h1', null, 'Bandas'),
    desenharFiltros(),
    desenharLancamentos(bandas),
    desenharListaBandas(bandas),
    origem === 'bands.txt' ? el('p', { class: 'nota' }, 'Lista de bandas: ficheiro bands.txt (Spotify por ligar).') : null,
    el('p', { class: 'nota' }, 'Lançamentos: MusicBrainz e Spotify.'),
  );
}

// --- Ecrã de uma banda ----------------------------------------------------------

function desenharBanda(id) {
  const banda = dados.bandas.find((item) => item.id === id);
  if (!banda) {
    location.hash = '#bandas';
    return;
  }
  const lancamentos = el('ul', { class: 'lista' });
  for (const lancamento of banda.lancamentos) {
    lancamentos.append(el('li', null, linhaLancamento(lancamento, null)));
  }
  const spotify = linkSeguro(banda.spotify);

  conteudo.replaceChildren(
    el('button', { type: 'button', class: 'botao-voltar', aoTocar: () => { location.hash = '#bandas'; } }, '‹ Bandas'),
    el('h1', null, banda.nome),
    el('p', { class: 'subtitulo' }, banda.categoria),
    spotify ? el('a', { class: 'botao-secundario', href: spotify, target: '_blank', rel: 'noopener noreferrer' }, 'Abrir no Spotify ↗') : null,
    el('h2', null, 'Lançamentos'),
    banda.lancamentos.length ? lancamentos : el('p', { class: 'vazio' }, 'Sem lançamentos encontrados.'),
  );
}

// --- Ecrã Concertos -------------------------------------------------------------

function desenharConcertos() {
  // Um festival aparece em várias bandas: junta-as num só concerto.
  const unicos = new Map();
  for (const banda of bandasFiltradas()) {
    for (const concerto of banda.concertos) {
      if (!unicos.has(concerto.id)) unicos.set(concerto.id, { concerto, nomes: [] });
      unicos.get(concerto.id).nomes.push(banda.nome);
    }
  }
  const ordenados = [...unicos.values()].sort((a, b) => (a.concerto.data > b.concerto.data ? 1 : -1));
  const lista = el('ul', { class: 'lista' });
  for (const { concerto, nomes } of ordenados) {
    lista.append(el('li', null, linhaConcerto(concerto, nomes.join(', '))));
  }
  conteudo.replaceChildren(
    el('h1', null, 'Concertos'),
    desenharFiltros(),
    el('h2', null, 'Próximos concertos em Portugal'),
    ordenados.length ? lista : el('p', { class: 'vazio' }, mensagemSemConcertos()),
    el('p', { class: 'nota' }, 'Concertos: Bandsintown.'),
  );
}

// --- Ecrã Notícias --------------------------------------------------------------

function desenharNoticias() {
  const lista = el('ul', { class: 'lista' });
  for (const artigo of dados.noticias) {
    const data = new Date(artigo.data);
    const quando = Number.isNaN(data.getTime()) ? '' : `${tempoPassado(data)} · ${diaMes(data)}`;
    lista.append(el('li', null, linhaLink(
      artigo.link,
      el('span', { class: 'linha-texto' },
        el('span', { class: 'linha-titulo' }, artigo.titulo),
        el('span', { class: 'linha-detalhe' }, [artigo.fonte, quando].filter(Boolean).join(' · '))),
      el('span', { class: 'seta', 'aria-hidden': 'true' }, '↗'),
    )));
  }
  conteudo.replaceChildren(
    el('h1', null, 'Notícias'),
    dados.noticias.length ? lista : el('p', { class: 'vazio' }, 'Sem notícias de momento.'),
  );
}

// --- Navegação -------------------------------------------------------------------

function rotaAtual() {
  const hash = location.hash.slice(1);
  if (hash === 'noticias') return { separador: 'noticias' };
  if (hash === 'concertos') return { separador: 'concertos' };
  if (hash.startsWith('banda/')) {
    try {
      return { separador: 'bandas', banda: decodeURIComponent(hash.slice(6)) };
    } catch (erro) {
      return { separador: 'bandas' };
    }
  }
  return { separador: 'bandas' };
}

function desenhar() {
  const rota = rotaAtual();
  document.querySelectorAll('.navegacao button').forEach((botao) => {
    if (botao.dataset.separador === rota.separador) botao.setAttribute('aria-current', 'page');
    else botao.removeAttribute('aria-current');
  });
  desenharAtualizado();
  if (!dados) {
    conteudo.replaceChildren(el('p', { class: 'vazio' }, erroCarregar
      ? 'Não foi possível carregar os dados. Verifica a internet e volta a abrir a app.'
      : 'A carregar…'));
    return;
  }
  if (rota.separador === 'noticias') desenharNoticias();
  else if (rota.separador === 'concertos') desenharConcertos();
  else if (rota.banda) desenharBanda(rota.banda);
  else desenharBandas();
}

let rotaAnterior = rotaAtual();
window.addEventListener('hashchange', () => {
  const rota = rotaAtual();
  desenhar();
  // Ao voltar de uma banda, regressa ao ponto da lista onde estava.
  window.scrollTo(0, rotaAnterior.banda && !rota.banda && rota.separador === 'bandas' ? scrollDaLista : 0);
  rotaAnterior = rota;
});

document.querySelectorAll('.navegacao button').forEach((botao) => {
  botao.addEventListener('click', () => {
    const destino = `#${botao.dataset.separador}`;
    if (location.hash === destino) window.scrollTo({ top: 0, behavior: 'smooth' });
    else location.hash = destino;
  });
});

// --- Dados -----------------------------------------------------------------------

function dadosValidos(json) {
  return json && Array.isArray(json.bandas) && Array.isArray(json.noticias);
}

async function carregar() {
  try {
    const resposta = await fetch('data.json', { cache: 'no-cache' });
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    const json = await resposta.json();
    if (!dadosValidos(json)) throw new Error('data.json inválido');
    dados = json;
    erroCarregar = false;
    carregadoEm = Date.now();
  } catch (erro) {
    erroCarregar = true;
  }
  const scroll = window.scrollY;
  desenhar();
  window.scrollTo(0, scroll);
}

// A app fica aberta em segundo plano no iPhone: ao voltar, procura dados novos.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (Date.now() - carregadoEm > MILISSEGUNDOS_HORA) carregar();
  else desenharAtualizado();
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

desenhar();
carregar();

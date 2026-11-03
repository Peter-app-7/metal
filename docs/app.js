// app.js: lê o data.json e desenha os ecrãs. A página não fala com mais nenhum serviço.
// Tudo o que vem de fora entra como texto (nunca como HTML) e os links são verificados.
'use strict';

const CATEGORIAS = ['Thrash', 'Death', 'Black', 'Doom/Sludge', 'Nu/Metalcore', 'Progressivo', 'Grunge', 'Outros'];
const FILTROS = ['Todas', ...CATEGORIAS];
const DIAS_RECENTES = 365;
const LANCAMENTOS_POR_PAGINA = 30;
const HORAS_ANTIGO = 24;
const MILISSEGUNDOS_HORA = 60 * 60 * 1000;
const FICHEIROS_DA_APP = ['index.html', 'app.js', 'style.css'];
const INTERVALO_VERIFICACOES = 2000;

const conteudo = document.getElementById('conteudo');
const atualizado = document.getElementById('atualizado');

let dados = null;
let erroCarregar = false;
let textoDados = null;
let ficheirosCarregados = null;
let ultimaVerificacao = Date.now();
let filtro = lerFiltro();
let lancamentosVisiveis = LANCAMENTOS_POR_PAGINA;
let scrollDaLista = 0;
// Só em memória: a lista de bandas começa sempre fechada quando a app abre.
let listaBandasAberta = false;

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

// Troca o conteúdo do ecrã, ignorando as peças vazias: replaceChildren escreveria "null" como texto.
function mostrar(...pecas) {
  conteudo.replaceChildren(...pecas.filter(Boolean));
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

// A data vem só com o dia, sem fuso: é a hora local.
function lerDataConcerto(texto) {
  const partes = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(texto || ''));
  if (!partes) return null;
  const [, ano, mes, dia] = partes;
  return { data: new Date(Number(ano), Number(mes) - 1, Number(dia)) };
}

function hojeTexto() {
  const hoje = new Date();
  const mes = String(hoje.getMonth() + 1).padStart(2, '0');
  const dia = String(hoje.getDate()).padStart(2, '0');
  return `${hoje.getFullYear()}-${mes}-${dia}`;
}

function eConcertoFuturo(concerto) {
  return String(concerto.data).slice(0, 10) >= hojeTexto();
}

function linhaConcerto(concerto) {
  const lida = lerDataConcerto(concerto.data);
  const data = lida && lida.data;
  const ano = data && data.getFullYear() !== new Date().getFullYear() ? ` ${data.getFullYear()}` : '';
  const local = [concerto.sala, concerto.cidade].filter(Boolean).join(' · ');
  const titulo = el('span', { class: 'linha-titulo concerto-bandas' });
  concerto.bandas.forEach((banda, indice) => {
    if (indice) titulo.append(' + ');
    titulo.append(el('span', { class: banda.minha ? 'banda-minha' : 'banda-outra' }, banda.nome));
  });
  return linhaLink(
    concerto.link,
    el('span', { class: 'dia' },
      el('span', { class: 'dia-semana' }, data ? DIAS_SEMANA[data.getDay()] : ''),
      el('span', { class: 'dia-numero' }, data ? String(data.getDate()) : '?'),
      el('span', { class: 'dia-mes' }, data ? `${MESES[data.getMonth()]}${ano}` : '')),
    el('span', { class: 'linha-texto' },
      titulo,
      concerto.evento ? el('span', { class: 'linha-detalhe' }, concerto.evento) : null,
      el('span', { class: 'linha-detalhe' }, local),
      el('span', { class: 'linha-detalhe concerto-fonte' }, `Fonte: ${concerto.fontes.join(' · ')}`)),
    el('span', { class: 'etiqueta-bilhetes' }, 'Agenda ↗'),
  );
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
  const interior = el('div', { class: 'recolhivel-interior' });
  if (!bandas.length) interior.append(el('p', { class: 'vazio' }, 'Sem bandas nesta categoria.'));
  for (const categoria of CATEGORIAS) {
    const grupo = bandas
      .filter((banda) => banda.categoria === categoria)
      .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-PT', { sensitivity: 'base' }));
    if (!grupo.length) continue;
    interior.append(el('h3', null, `${categoria} (${grupo.length})`));
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
    interior.append(lista);
  }

  const conteudoLista = el('div', { id: 'lista-bandas', class: 'recolhivel' }, interior);
  const botao = el('button', {
    type: 'button',
    class: 'linha titulo-recolhivel',
    'aria-controls': 'lista-bandas',
    aoTocar: () => {
      // Muda só os atributos, sem redesenhar, para a animação poder correr.
      listaBandasAberta = !listaBandasAberta;
      botao.setAttribute('aria-expanded', String(listaBandasAberta));
      conteudoLista.dataset.aberto = String(listaBandasAberta);
    },
  },
  el('span', { class: 'linha-texto' }, `Todas as bandas (${bandas.length})`),
  el('span', { class: 'seta seta-recolhivel', 'aria-hidden': 'true' }, '›'));
  botao.setAttribute('aria-expanded', String(listaBandasAberta));
  conteudoLista.dataset.aberto = String(listaBandasAberta);

  return el('section', null, el('h2', { class: 'titulo-com-botao' }, botao), conteudoLista);
}

function desenharBandas() {
  const bandas = bandasFiltradas();
  const origem = dados.fontes && dados.fontes.bandas;
  mostrar(
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

  mostrar(
    el('button', { type: 'button', class: 'botao-voltar', aoTocar: () => { location.hash = '#bandas'; } }, '‹ Bandas'),
    el('h1', null, banda.nome),
    el('p', { class: 'subtitulo' }, banda.categoria),
    spotify ? el('a', { class: 'botao-secundario', href: spotify, target: '_blank', rel: 'noopener noreferrer' }, 'Abrir no Spotify ↗') : null,
    el('h2', null, 'Lançamentos'),
    banda.lancamentos.length ? lancamentos : el('p', { class: 'vazio' }, 'Sem lançamentos encontrados.'),
  );
}

// --- Ecrã Concertos -------------------------------------------------------------

// "sáb, 14 nov 2026" para um dia, "6 a 7 nov 2026" ou "28 nov a 2 dez 2026" para um intervalo.
function datasFestival(festival) {
  const inicio = lerDataConcerto(festival.data_inicio);
  const fim = lerDataConcerto(festival.data_fim) || inicio;
  if (!inicio) return { numero: '?', mes: '', semana: '', texto: '' };
  const a = inicio.data;
  const b = fim.data;
  const mesmoDia = a.getTime() === b.getTime();
  const mesmoMes = a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear();
  const anoFim = b.getFullYear();
  const anoA = a.getFullYear() !== anoFim ? ` ${a.getFullYear()}` : '';
  if (mesmoDia) {
    return { numero: String(a.getDate()), mes: MESES[a.getMonth()], semana: DIAS_SEMANA[a.getDay()], texto: `${DIAS_SEMANA[a.getDay()]}, ${diaMes(a)} ${anoFim}` };
  }
  return {
    numero: mesmoMes ? `${a.getDate()}–${b.getDate()}` : String(a.getDate()),
    mes: MESES[a.getMonth()],
    semana: '',
    texto: mesmoMes ? `${a.getDate()} a ${diaMes(b)} ${anoFim}` : `${diaMes(a)}${anoA} a ${diaMes(b)} ${anoFim}`,
  };
}

function linhaFestival(festival) {
  const datas = datasFestival(festival);
  const local = [festival.local, festival.cidade].filter(Boolean).join(' · ');
  const cartaz = el('span', { class: 'linha-detalhe concerto-bandas' });
  festival.bandas.forEach((banda, indice) => {
    if (indice) cartaz.append(' + ');
    cartaz.append(el('span', { class: banda.minha ? 'banda-minha' : 'banda-outra' }, banda.nome));
  });
  const soAgenda = festival.fontes.length === 1 && festival.fontes[0] === 'Caminhos Metálicos';
  return linhaLink(
    festival.link,
    el('span', { class: 'dia' },
      datas.semana ? el('span', { class: 'dia-semana' }, datas.semana) : null,
      el('span', { class: datas.numero.length > 2 ? 'dia-numero dia-numero-intervalo' : 'dia-numero' }, datas.numero),
      el('span', { class: 'dia-mes' }, datas.mes)),
    el('span', { class: 'linha-texto' },
      el('span', { class: 'linha-titulo' }, festival.nome),
      festival.tem_minhas ? el('span', { class: 'etiqueta-minhas' }, 'tens bandas aqui') : null,
      el('span', { class: 'linha-detalhe' }, datas.texto),
      local ? el('span', { class: 'linha-detalhe' }, local) : null,
      festival.bandas.length ? cartaz : null),
    linkSeguro(festival.link) ? el('span', { class: 'etiqueta-bilhetes' }, soAgenda ? 'Agenda ↗' : 'Ver ↗') : null,
  );
}

function desenharFestivais() {
  const hoje = hojeTexto();
  const futuros = (dados.festivais || []).filter((f) => String(f.data_fim) >= hoje);
  const seccao = el('section', null, el('h2', null, 'Festivais em Portugal'));
  if (!futuros.length) {
    seccao.append(el('p', { class: 'vazio' }, 'Sem festivais anunciados'));
    return seccao;
  }
  const lista = el('ul', { class: 'lista' });
  for (const festival of futuros) lista.append(el('li', null, linhaFestival(festival)));
  seccao.append(lista);
  return seccao;
}

function desenharConcertos() {
  const agenda = (dados.fontes && dados.fontes.agenda) || {};
  const concertos = (dados.concertos || []).filter(eConcertoFuturo);
  const lista = el('ul', { class: 'lista' });
  for (const concerto of concertos) lista.append(el('li', null, linhaConcerto(concerto)));

  const semConcertos = !concertos.length;
  const mensagem = 'Nenhum concerto das tuas bandas na agenda neste momento';
  const dataAgenda = lerDataConcerto(agenda.atualizada);
  const site = el('a', {
    class: 'ligacao-fonte',
    href: 'https://www.caminhosmetalicos.com/',
    target: '_blank',
    rel: 'noopener noreferrer',
  }, 'Agenda: Caminhos Metálicos');

  mostrar(
    el('h1', null, 'Concertos'),
    el('h2', null, 'Os meus concertos'),
    agenda.estado === 'indisponivel' ? el('p', { class: 'aviso-agenda' }, 'Agenda temporariamente indisponível') : null,
    // Com a agenda em baixo e nada guardado, não se sabe se há concertos: fica só o aviso.
    !semConcertos ? lista : (agenda.estado === 'indisponivel' ? null : el('p', { class: 'vazio' }, mensagem)),
    desenharFestivais(),
    site,
    dataAgenda ? el('p', { class: 'nota nota-junta' }, `Agenda atualizada pelo site a ${diaMes(dataAgenda.data)} ${dataAgenda.data.getFullYear()}.`) : null,
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
    const texto = await resposta.text();
    // Sem mudanças, só acerta o "há X min": assim o ecrã não pisca ao voltar à app.
    if (texto === textoDados && !erroCarregar) {
      desenharAtualizado();
      return;
    }
    const json = JSON.parse(texto);
    if (!dadosValidos(json)) throw new Error('data.json inválido');
    dados = json;
    textoDados = texto;
    erroCarregar = false;
  } catch (erro) {
    erroCarregar = true;
  }
  const scroll = window.scrollY;
  desenhar();
  window.scrollTo(0, scroll);
}

// --- Apanhar versões novas -------------------------------------------------------
// A app do ecrã principal fica congelada em memória e, ao voltar, continua com o código antigo.
// Por isso compara os ficheiros da app com os que carregou e recarrega se algum tiver mudado.
// Não há número de versão para manter à mão: qualquer publicação é detetada.

async function lerFicheirosDaApp() {
  const textos = await Promise.all(FICHEIROS_DA_APP.map(async (ficheiro) => {
    const resposta = await fetch(ficheiro, { cache: 'no-cache' });
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    return resposta.text();
  }));
  return textos.join('\n');
}

async function procurarVersaoNova() {
  try {
    const atual = await lerFicheirosDaApp();
    if (ficheirosCarregados === null) ficheirosCarregados = atual;
    else if (atual !== ficheirosCarregados) location.reload();
  } catch (erro) {
    // Sem ligação: tenta na próxima vez que voltar à app.
  }
}

function aoVoltarParaAApp() {
  if (Date.now() - ultimaVerificacao < INTERVALO_VERIFICACOES) return;
  ultimaVerificacao = Date.now();
  desenharAtualizado();
  carregar();
  procurarVersaoNova();
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistration().then((registo) => registo && registo.update()).catch(() => {});
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') aoVoltarParaAApp();
});
window.addEventListener('pageshow', (evento) => {
  if (evento.persisted) aoVoltarParaAApp();
});
window.addEventListener('online', aoVoltarParaAApp);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' }).catch(() => {});
}

desenhar();
carregar();
procurarVersaoNova();

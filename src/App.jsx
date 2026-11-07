import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import Icon from './components/Icon'
import { Alert } from './components/ui'
import { ConfirmDialog, RemessaFormModal, Toasts } from './components/Overlays'
import RemessaDrawer from './components/RemessaDrawer'
import VisaoGeral from './pages/VisaoGeral'
import QuadroRemessas from './pages/QuadroRemessas'
import ProducaoDiaria from './pages/ProducaoDiaria'
import Programacao from './pages/Programacao'
import ImportarExcel from './pages/ImportarExcel'
import { STATUS_EXCLUIDA, formatarNumero, mensagemErro, requisitar } from './pcpUtils'

const menu = [
  {
    grupo: 'Operação',
    itens: [
      { nome: 'Visão geral', icone: 'dashboard', id: 'nav-visao-geral' },
      { nome: 'Quadro de remessas', icone: 'board', id: 'nav-quadro' },
      { nome: 'Produção diária', icone: 'activity', id: 'nav-producao' },
      { nome: 'Programação', icone: 'calendar', id: 'nav-programacao' },
    ],
  },
  {
    grupo: 'Dados',
    itens: [{ nome: 'Importar Excel', icone: 'upload', id: 'nav-importar' }],
  },
]

// Cabeçalho de cada página do menu.
const paginasInfo = {
  'Visão geral': {
    titulo: 'Visão geral da fábrica',
    descricao: 'Indicadores das remessas ativas e andamento da produção por etapa.',
  },
  'Quadro de remessas': {
    titulo: 'Quadro de remessas',
    descricao: 'Cadastre, edite, exclua e consulte o histórico das remessas.',
  },
  'Produção diária': {
    titulo: 'Produção diária',
    descricao: 'Registre a produção por etapa e consulte os lançamentos individuais.',
  },
  Programação: {
    titulo: 'Programação de entregas',
    descricao: 'Remessas ativas ordenadas pela data de entrega, com o avanço registrado por etapa.',
  },
  'Importar Excel': {
    titulo: 'Importar Excel',
    descricao: 'Importação de remessas a partir de planilhas.',
  },
}

const formularioVazio = {
  numero: '',
  descricao: '',
  data_entrega: '',
  quantidade_pares: '',
  status: 'A programar',
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function App() {
  const [pagina, setPagina] = useState('Visão geral')
  const [menuAberto, setMenuAberto] = useState(false)

  // Dados da API
  const [remessas, setRemessas] = useState([])
  const [carregandoRemessas, setCarregandoRemessas] = useState(true)
  const [erroRemessas, setErroRemessas] = useState('')

  const [resumoProducao, setResumoProducao] = useState([])
  const [carregandoResumo, setCarregandoResumo] = useState(true)
  const [erroResumo, setErroResumo] = useState('')

  const [lancamentos, setLancamentos] = useState([])
  const [carregandoLancamentos, setCarregandoLancamentos] = useState(true)
  const [erroLancamentos, setErroLancamentos] = useState('')

  const [apiOnline, setApiOnline] = useState(null)
  const [ultimaAtualizacao, setUltimaAtualizacao] = useState(null)

  // Avisos
  const [toasts, setToasts] = useState([])
  const idToast = useRef(0)

  // Formulário de remessa
  const [modalAberto, setModalAberto] = useState(false)
  const [remessaEditando, setRemessaEditando] = useState(null)
  const [formulario, setFormulario] = useState(formularioVazio)
  const [salvando, setSalvando] = useState(false)
  const [erroFormulario, setErroFormulario] = useState('')

  // Exclusão lógica
  const [remessaExcluindo, setRemessaExcluindo] = useState(null)
  const [excluindo, setExcluindo] = useState(false)

  // Detalhes + histórico
  const [remessaDetalhe, setRemessaDetalhe] = useState(null)
  const [historico, setHistorico] = useState([])
  const [carregandoHistorico, setCarregandoHistorico] = useState(false)
  const [erroHistorico, setErroHistorico] = useState('')

  const fecharToast = useCallback((id) => setToasts((lista) => lista.filter((t) => t.id !== id)), [])

  const avisar = useCallback(
    (texto, tipo = 'success') => {
      idToast.current += 1
      const id = idToast.current
      setToasts((lista) => [...lista, { id, texto, tipo }])
      window.setTimeout(() => fecharToast(id), tipo === 'error' ? 8000 : 4500)
    },
    [fecharToast],
  )

  // ---------- Carregamento (GET) ----------
  const sincronizarRemessas = useCallback(
    () =>
      requisitar('/remessas', {}, 'Não foi possível carregar as remessas.')
        .then((dados) => {
          setRemessas(Array.isArray(dados) ? dados : [])
          setErroRemessas('')
          setApiOnline(true)
        })
        .catch((e) => {
          setErroRemessas(mensagemErro(e, 'Não foi possível carregar as remessas.'))
          if (e instanceof TypeError) setApiOnline(false)
        })
        .finally(() => setCarregandoRemessas(false)),
    [],
  )

  const sincronizarResumo = useCallback(
    () =>
      requisitar('/producao/resumo', {}, 'Não foi possível carregar o resumo da produção.')
        .then((dados) => {
          setResumoProducao(Array.isArray(dados) ? dados : [])
          setErroResumo('')
        })
        .catch((e) => {
          setResumoProducao([])
          setErroResumo(mensagemErro(e, 'Não foi possível carregar o resumo da produção.'))
        })
        .finally(() => setCarregandoResumo(false)),
    [],
  )

  const sincronizarLancamentos = useCallback(
    () =>
      requisitar('/producao', {}, 'Não foi possível carregar os lançamentos de produção.')
        .then((dados) => {
          setLancamentos(Array.isArray(dados) ? dados : [])
          setErroLancamentos('')
        })
        .catch((e) => {
          setLancamentos([])
          setErroLancamentos(mensagemErro(e, 'Não foi possível carregar os lançamentos de produção.'))
        })
        .finally(() => setCarregandoLancamentos(false)),
    [],
  )

  const sincronizarTudo = useCallback(
    () =>
      Promise.all([sincronizarRemessas(), sincronizarResumo(), sincronizarLancamentos()]).then(() =>
        setUltimaAtualizacao(new Date()),
      ),
    [sincronizarRemessas, sincronizarResumo, sincronizarLancamentos],
  )

  useEffect(() => {
    sincronizarTudo()
  }, [sincronizarTudo])

  function atualizarTudo() {
    setCarregandoRemessas(true)
    setCarregandoResumo(true)
    setCarregandoLancamentos(true)
    sincronizarTudo()
  }

  function recarregarRemessas() {
    setCarregandoRemessas(true)
    sincronizarRemessas()
  }

  function recarregarResumo() {
    setCarregandoResumo(true)
    sincronizarResumo()
  }

  function recarregarLancamentos() {
    setCarregandoLancamentos(true)
    sincronizarLancamentos()
  }

  // ---------- Dados derivados ----------
  const resumoPorRemessa = useMemo(() => {
    const mapa = new Map()
    resumoProducao.forEach((item) => mapa.set(Number(item.remessa_id), item))
    return mapa
  }, [resumoProducao])

  const remessasAtivas = useMemo(() => remessas.filter((r) => r.status !== STATUS_EXCLUIDA), [remessas])
  const remessasExcluidas = useMemo(() => remessas.filter((r) => r.status === STATUS_EXCLUIDA), [remessas])

  // ---------- Navegação ----------
  function navegar(destino) {
    setPagina(destino)
    setMenuAberto(false)
    window.scrollTo({ top: 0 })
  }

  // ---------- Cadastro / edição (POST/PUT /api/remessas) ----------
  function abrirCadastro() {
    setRemessaEditando(null)
    setFormulario(formularioVazio)
    setErroFormulario('')
    setModalAberto(true)
  }

  function abrirEdicao(remessa) {
    setRemessaDetalhe(null)
    setRemessaEditando(remessa)
    setFormulario({
      numero: remessa.numero,
      descricao: remessa.descricao,
      data_entrega: remessa.data_entrega || '',
      quantidade_pares: String(remessa.quantidade_pares),
      status: remessa.status,
    })
    setErroFormulario('')
    setModalAberto(true)
  }

  const fecharModal = useCallback(() => {
    setModalAberto(false)
  }, [])

  function alterarCampo(evento) {
    const { name, value } = evento.target
    setFormulario((atual) => ({ ...atual, [name]: value }))
  }

  async function salvarRemessa(evento) {
    evento.preventDefault()
    if (salvando) return
    setErroFormulario('')

    const quantidade = Number(formulario.quantidade_pares)
    if (formulario.quantidade_pares === '' || !Number.isInteger(quantidade) || quantidade < 0) {
      setErroFormulario('Informe uma quantidade inteira de pares igual ou maior que zero.')
      return
    }

    const dados = {
      numero: formulario.numero.trim(),
      descricao: formulario.descricao.trim(),
      data_entrega: formulario.data_entrega || null,
      quantidade_pares: quantidade,
      status: formulario.status,
    }

    if (!dados.numero || !dados.descricao) {
      setErroFormulario('Preencha o número e a descrição da remessa.')
      return
    }

    setSalvando(true)
    try {
      const editando = Boolean(remessaEditando)
      await requisitar(
        editando ? `/remessas/${remessaEditando.id}` : '/remessas',
        { method: editando ? 'PUT' : 'POST', headers: JSON_HEADERS, body: JSON.stringify(dados) },
        'Não foi possível salvar a remessa.',
      )
      setModalAberto(false)
      avisar(
        editando
          ? `Remessa ${dados.numero} atualizada com sucesso.`
          : `Remessa ${dados.numero} cadastrada com sucesso.`,
      )
      await Promise.all([sincronizarRemessas(), sincronizarResumo()])
    } catch (e) {
      setErroFormulario(mensagemErro(e, 'Erro ao comunicar com a API.'))
    } finally {
      setSalvando(false)
    }
  }

  // ---------- Exclusão lógica (DELETE /api/remessas/{id}) ----------
  async function confirmarExclusao() {
    const remessa = remessaExcluindo
    if (!remessa || excluindo) return
    setExcluindo(true)
    try {
      await requisitar(`/remessas/${remessa.id}`, { method: 'DELETE' }, 'Não foi possível excluir a remessa.')
      avisar(`Remessa ${remessa.numero} marcada como excluída.`)
      setRemessaExcluindo(null)
      await Promise.all([sincronizarRemessas(), sincronizarResumo()])
    } catch (e) {
      avisar(mensagemErro(e, 'Erro ao comunicar com a API.'), 'error')
    } finally {
      setExcluindo(false)
    }
  }

  // ---------- Histórico (GET /api/remessas/{id}/historico) ----------
  async function consultarHistorico(remessaId) {
    const idNumerico = Number(remessaId)
    if (!Number.isInteger(idNumerico) || idNumerico <= 0) {
      setHistorico([])
      setErroHistorico('ID da remessa inválido para consulta de histórico.')
      return
    }
    setCarregandoHistorico(true)
    setErroHistorico('')
    try {
      const resultado = await requisitar(
        `/remessas/${idNumerico}/historico`,
        {},
        'Não foi possível carregar o histórico da remessa.',
      )
      setHistorico(Array.isArray(resultado) ? resultado : [])
    } catch (e) {
      setHistorico([])
      setErroHistorico(
        e.status === 404 ? 'Remessa não encontrada.' : mensagemErro(e, 'Não foi possível carregar o histórico da remessa.'),
      )
    } finally {
      setCarregandoHistorico(false)
    }
  }

  function abrirDetalhes(remessa) {
    setRemessaDetalhe(remessa)
    setHistorico([])
    setErroHistorico('')
    consultarHistorico(remessa?.id)
  }

  const fecharDetalhes = useCallback(() => {
    setRemessaDetalhe(null)
    setHistorico([])
    setErroHistorico('')
  }, [])

  // Mantém o painel com os dados mais recentes da lista após atualizações.
  const remessaDetalheAtual = remessaDetalhe
    ? remessas.find((r) => Number(r.id) === Number(remessaDetalhe.id)) || remessaDetalhe
    : null

  // ---------- Registro de produção (POST /api/producao) ----------
  async function registrarProducao(dados) {
    const registro = await requisitar(
      '/producao',
      { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(dados) },
      'Não foi possível registrar a produção.',
    )
    avisar(
      `Lançamento registrado: ${registro.etapa} · ${formatarNumero(registro.quantidade_pares)} pares · #${registro.remessa_numero}`,
    )
    await Promise.all([sincronizarLancamentos(), sincronizarResumo()])
    setUltimaAtualizacao(new Date())
  }

  const algumCarregando = carregandoRemessas || carregandoResumo || carregandoLancamentos
  const info = paginasInfo[pagina] || paginasInfo['Visão geral']

  const statusApi =
    apiOnline === null
      ? { classe: 'checking', texto: 'Verificando API...' }
      : apiOnline
        ? { classe: 'online', texto: 'API conectada' }
        : { classe: 'offline', texto: 'API indisponível' }

  return (
    <div className={`shell ${menuAberto ? 'menu-open' : ''}`}>
      <aside className="sidebar" aria-label="Menu principal">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="factory" size={20} strokeWidth={2} />
          </span>
          <div>
            <strong>PCP Industrial</strong>
            <span>Planejamento e controle</span>
          </div>
        </div>

        <nav className="nav">
          {menu.map((grupo) => (
            <div className="nav-group" key={grupo.grupo}>
              <span className="nav-label">{grupo.grupo}</span>
              {grupo.itens.map((item) => (
                <button
                  key={item.nome}
                  id={item.id}
                  type="button"
                  className={`nav-item ${pagina === item.nome ? 'active' : ''}`}
                  aria-current={pagina === item.nome ? 'page' : undefined}
                  onClick={() => navegar(item.nome)}
                >
                  <Icon name={item.icone} size={18} />
                  <span>{item.nome}</span>
                  {item.nome === 'Quadro de remessas' && remessasAtivas.length > 0 && (
                    <span className="nav-count">{remessasAtivas.length}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className={`api-status ${statusApi.classe}`}>
            <span className="api-dot" />
            <div>
              <strong>{statusApi.texto}</strong>
              <small>127.0.0.1:8000</small>
            </div>
          </div>
          <small className="version">Versão 0.7 · Ambiente local</small>
        </div>
      </aside>

      {menuAberto && <div className="sidebar-scrim" onClick={() => setMenuAberto(false)} aria-hidden="true" />}

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn menu-toggle"
            onClick={() => setMenuAberto((a) => !a)}
            aria-label="Abrir menu"
          >
            <Icon name="menu" size={20} />
          </button>
          <nav className="breadcrumb" aria-label="Trilha de navegação">
            <span>PCP Industrial</span>
            <Icon name="chevronRight" size={14} />
            <strong>{pagina}</strong>
          </nav>
          <div className="topbar-right">
            <span className="sync-info">
              {algumCarregando
                ? 'Sincronizando...'
                : ultimaAtualizacao
                  ? `Atualizado às ${ultimaAtualizacao.toLocaleTimeString('pt-BR')}`
                  : ''}
            </span>
            <button
              id="topbar-atualizar"
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={atualizarTudo}
              disabled={algumCarregando}
            >
              <Icon name="refresh" size={14} className={algumCarregando ? 'spin' : ''} />
              <span className="hide-sm">Atualizar dados</span>
            </button>
            <div className="user-chip">
              <span className="avatar">PC</span>
              <div>
                <strong>Planejamento</strong>
                <small>Administrador</small>
              </div>
            </div>
          </div>
        </header>

        <main className="page" key={pagina}>
          <div className="page-header">
            <div>
              <h1>{info.titulo}</h1>
              <p>{info.descricao}</p>
            </div>
            {(pagina === 'Visão geral' || pagina === 'Quadro de remessas' || pagina === 'Programação') && (
              <div className="page-actions">
                <button
                  id="nova-remessa"
                  type="button"
                  className={`btn ${pagina === 'Quadro de remessas' ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={abrirCadastro}
                >
                  <Icon name="plus" size={16} />
                  Nova remessa
                </button>
              </div>
            )}
          </div>

          {erroRemessas && (
            <Alert
              tipo="error"
              titulo="Falha ao carregar as remessas"
              acao={
                <button type="button" className="btn btn-secondary btn-sm" onClick={recarregarRemessas}>
                  Tentar novamente
                </button>
              }
            >
              {erroRemessas}
            </Alert>
          )}

          {erroResumo && ['Visão geral', 'Quadro de remessas', 'Programação', 'Produção diária'].includes(pagina) && (
            <Alert
              tipo="error"
              titulo="Não foi possível atualizar os totais de produção"
              acao={
                <button type="button" className="btn btn-secondary btn-sm" onClick={recarregarResumo}>
                  Tentar novamente
                </button>
              }
            >
              {erroResumo}
            </Alert>
          )}

          {erroLancamentos && pagina === 'Visão geral' && (
            <Alert
              tipo="error"
              titulo="Não foi possível carregar os lançamentos"
              acao={
                <button type="button" className="btn btn-secondary btn-sm" onClick={recarregarLancamentos}>
                  Tentar novamente
                </button>
              }
            >
              {erroLancamentos}
            </Alert>
          )}

          {pagina === 'Visão geral' && (
            <VisaoGeral
              remessasAtivas={remessasAtivas}
              remessasExcluidas={remessasExcluidas}
              resumoProducao={resumoProducao}
              resumoPorRemessa={resumoPorRemessa}
              lancamentos={lancamentos}
              carregando={carregandoRemessas}
              carregandoResumo={carregandoResumo}
              carregandoLancamentos={carregandoLancamentos}
              onNavegar={navegar}
              onVisualizar={abrirDetalhes}
            />
          )}

          {pagina === 'Quadro de remessas' && (
            <QuadroRemessas
              remessasAtivas={remessasAtivas}
              remessasExcluidas={remessasExcluidas}
              resumoPorRemessa={resumoPorRemessa}
              carregando={carregandoRemessas}
              onNova={abrirCadastro}
              onVisualizar={abrirDetalhes}
              onEditar={abrirEdicao}
              onExcluir={setRemessaExcluindo}
            />
          )}

          {pagina === 'Produção diária' && (
            <ProducaoDiaria
              remessas={remessas}
              remessasAtivas={remessasAtivas}
              resumoPorRemessa={resumoPorRemessa}
              lancamentos={lancamentos}
              carregando={carregandoLancamentos}
              erro={erroLancamentos}
              onAtualizar={recarregarLancamentos}
              onRegistrar={registrarProducao}
            />
          )}

          {pagina === 'Programação' && (
            <Programacao
              remessas={remessas}
              resumoPorRemessa={resumoPorRemessa}
              carregando={carregandoRemessas}
              onEditar={abrirEdicao}
              onVisualizar={abrirDetalhes}
            />
          )}

          {pagina === 'Importar Excel' && <ImportarExcel onNavegar={navegar} onNovaRemessa={abrirCadastro} />}
        </main>
      </div>

      {modalAberto && (
        <RemessaFormModal
          remessa={remessaEditando}
          formulario={formulario}
          onCampo={alterarCampo}
          onSubmit={salvarRemessa}
          onFechar={fecharModal}
          salvando={salvando}
          erro={erroFormulario}
        />
      )}

      {remessaExcluindo && (
        <ConfirmDialog
          titulo={`Excluir a remessa #${remessaExcluindo.numero}?`}
          rotuloConfirmar={excluindo ? 'Excluindo...' : 'Excluir remessa'}
          perigo
          ocupado={excluindo}
          onConfirmar={confirmarExclusao}
          onCancelar={() => setRemessaExcluindo(null)}
        >
          A remessa será marcada como <strong>excluída</strong> e sairá dos totais e da programação. O registro e o
          histórico continuam preservados no banco para consulta.
        </ConfirmDialog>
      )}

      {remessaDetalheAtual && (
        <RemessaDrawer
          remessa={remessaDetalheAtual}
          producao={resumoPorRemessa.get(Number(remessaDetalheAtual.id))}
          lancamentos={lancamentos.filter((l) => Number(l.remessa_id) === Number(remessaDetalheAtual.id))}
          historico={historico}
          carregando={carregandoHistorico}
          erro={erroHistorico}
          onRecarregar={() => consultarHistorico(remessaDetalheAtual.id)}
          onFechar={fecharDetalhes}
          onEditar={abrirEdicao}
        />
      )}

      <Toasts itens={toasts} onFechar={fecharToast} />
    </div>
  )
}

export default App
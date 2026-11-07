import { useMemo, useRef, useState } from 'react'
import Icon from '../components/Icon'
import { Alert, Card, EmptyState, SkeletonRows, StageIcon, StageTag } from '../components/ui'
import {
  ETAPAS,
  STATUS_EXCLUIDA,
  formatarData,
  formatarDataHora,
  formatarNumero,
  hojeISO,
  mensagemErro,
} from '../pcpUtils'

const filtrosVazios = { dataInicio: '', dataFim: '', remessaId: '', etapa: '' }
const chaveEtapa = { Corte: 'corte', Costura: 'costura', Montagem: 'montagem' }
const LIMITE_OBSERVACAO = 500

function novoFormulario() {
  return { remessaId: '', etapa: 'Corte', data: hojeISO(), quantidade: '', observacao: '' }
}

function ProducaoDiaria({
  remessas,
  remessasAtivas,
  resumoPorRemessa,
  lancamentos,
  carregando,
  erro,
  onAtualizar,
  onRegistrar,
}) {
  const [form, setForm] = useState(novoFormulario)
  const [erroForm, setErroForm] = useState('')
  const [enviando, setEnviando] = useState(false)
  // Trava síncrona contra duplo clique/duplo envio (evita lançamentos duplicados).
  const enviandoRef = useRef(false)
  const [filtros, setFiltros] = useState(filtrosVazios)

  function alterarForm(evento) {
    const { name, value } = evento.target
    setForm((atual) => ({ ...atual, [name]: value }))
  }

  const remessaSelecionada = remessasAtivas.find((r) => Number(r.id) === Number(form.remessaId))
  const producaoSelecionada = remessaSelecionada ? resumoPorRemessa.get(Number(remessaSelecionada.id)) || {} : null
  const qtdDigitada = Number(form.quantidade)
  const jaLancadoEtapa = producaoSelecionada ? Number(producaoSelecionada[chaveEtapa[form.etapa]] || 0) : 0
  const ultrapassa =
    remessaSelecionada &&
    Number.isInteger(qtdDigitada) &&
    qtdDigitada > 0 &&
    jaLancadoEtapa + qtdDigitada > Number(remessaSelecionada.quantidade_pares || 0)
  const dataFutura = form.data && form.data > hojeISO()

  async function enviar(evento) {
    evento.preventDefault()
    if (enviandoRef.current) return
    setErroForm('')

    const quantidade = Number(form.quantidade)
    if (!form.remessaId) return setErroForm('Selecione a remessa.')
    if (!ETAPAS.includes(form.etapa)) return setErroForm('Selecione a etapa.')
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.data)) return setErroForm('Informe a data de produção.')
    if (!Number.isInteger(quantidade) || quantidade <= 0) {
      return setErroForm('Informe uma quantidade inteira de pares maior que zero.')
    }
    if (form.observacao.length > LIMITE_OBSERVACAO) {
      return setErroForm(`A observação pode ter no máximo ${LIMITE_OBSERVACAO} caracteres.`)
    }

    enviandoRef.current = true
    setEnviando(true)
    try {
      await onRegistrar({
        remessa_id: Number(form.remessaId),
        etapa: form.etapa,
        data_producao: form.data,
        quantidade_pares: quantidade,
        observacao: form.observacao.trim(),
      })
      // Mantém remessa, etapa e data para agilizar lançamentos em sequência.
      setForm((atual) => ({ ...atual, quantidade: '', observacao: '' }))
    } catch (e) {
      setErroForm(mensagemErro(e, 'Não foi possível registrar a produção.'))
    } finally {
      enviandoRef.current = false
      setEnviando(false)
    }
  }

  // ---------- Consulta ----------
  function alterarFiltro(evento) {
    const { name, value } = evento.target
    setFiltros((atual) => ({ ...atual, [name]: value }))
  }

  const opcoesRemessa = useMemo(() => {
    const mapa = new Map()
    remessas.forEach((r) => mapa.set(Number(r.id), { id: Number(r.id), numero: r.numero, status: r.status }))
    lancamentos.forEach((l) => {
      if (!mapa.has(Number(l.remessa_id))) {
        mapa.set(Number(l.remessa_id), { id: Number(l.remessa_id), numero: l.remessa_numero, status: l.status_remessa })
      }
    })
    return [...mapa.values()].sort((a, b) => String(a.numero).localeCompare(String(b.numero), 'pt-BR'))
  }, [remessas, lancamentos])

  const periodoInvalido = Boolean(filtros.dataInicio && filtros.dataFim && filtros.dataInicio > filtros.dataFim)

  const filtrados = useMemo(() => {
    if (periodoInvalido) return []
    return lancamentos.filter((l) => {
      if (filtros.dataInicio && l.data_producao < filtros.dataInicio) return false
      if (filtros.dataFim && l.data_producao > filtros.dataFim) return false
      if (filtros.remessaId && Number(l.remessa_id) !== Number(filtros.remessaId)) return false
      if (filtros.etapa && l.etapa !== filtros.etapa) return false
      return true
    })
  }, [lancamentos, filtros, periodoInvalido])

  const totais = useMemo(() => {
    const base = { Corte: 0, Costura: 0, Montagem: 0 }
    filtrados.forEach((l) => {
      if (l.etapa in base) base[l.etapa] += Number(l.quantidade_pares || 0)
    })
    return base
  }, [filtrados])

  const filtrosAtivos = Object.values(filtros).some(Boolean)

  return (
    <div className="production-layout">
      {/* ---------- Registro ---------- */}
      <Card
        className="production-form-card"
        titulo="Registrar produção"
        subtitulo="Cada envio cria um novo lançamento individual."
        icone="plus"
      >
        <form className="production-form" onSubmit={enviar}>
          <label className="field">
            <span className="field-label">
              Remessa <em>*</em>
            </span>
            <select
              id="registro-remessa"
              className="input"
              name="remessaId"
              value={form.remessaId}
              onChange={alterarForm}
              disabled={enviando}
            >
              <option value="">Selecione uma remessa ativa</option>
              {remessasAtivas.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.numero} — {r.descricao}
                </option>
              ))}
            </select>
          </label>

          {remessaSelecionada && (
            <div className="remessa-context">
              <div className="remessa-context-head">
                <span>Planejado</span>
                <strong className="num">{formatarNumero(remessaSelecionada.quantidade_pares)} pares</strong>
              </div>
              <div className="remessa-context-stages">
                {ETAPAS.map((etapa) => (
                  <div key={etapa} className={form.etapa === etapa ? 'is-current' : ''}>
                    <span>{etapa}</span>
                    <strong className="num">{formatarNumero(producaoSelecionada?.[chaveEtapa[etapa]] || 0)}</strong>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="field">
            <span className="field-label" id="rotulo-etapa">
              Etapa <em>*</em>
            </span>
            <div className="stage-picker" role="radiogroup" aria-labelledby="rotulo-etapa">
              {ETAPAS.map((etapa) => (
                <label key={etapa} className={`stage-option stage-${chaveEtapa[etapa]} ${form.etapa === etapa ? 'checked' : ''}`}>
                  <input
                    type="radio"
                    name="etapa"
                    value={etapa}
                    checked={form.etapa === etapa}
                    onChange={alterarForm}
                    disabled={enviando}
                  />
                  <StageIcon etapa={etapa} size={16} />
                  <span>{etapa}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="form-grid">
            <label className="field">
              <span className="field-label">
                Data <em>*</em>
              </span>
              <input
                id="registro-data"
                className="input"
                type="date"
                name="data"
                value={form.data}
                onChange={alterarForm}
                disabled={enviando}
              />
            </label>
            <label className="field">
              <span className="field-label">
                Pares <em>*</em>
              </span>
              <input
                id="registro-quantidade"
                className="input num"
                type="number"
                min="1"
                step="1"
                name="quantidade"
                value={form.quantidade}
                onChange={alterarForm}
                placeholder="0"
                disabled={enviando}
              />
            </label>
          </div>

          <label className="field">
            <span className="field-label field-label-row">
              Observação
              <small className="num">
                {form.observacao.length}/{LIMITE_OBSERVACAO}
              </small>
            </span>
            <textarea
              id="registro-observacao"
              className="input textarea"
              name="observacao"
              rows={3}
              maxLength={LIMITE_OBSERVACAO}
              value={form.observacao}
              onChange={alterarForm}
              placeholder="Opcional — turno, operador, ocorrência..."
              disabled={enviando}
            />
          </label>

          {dataFutura && <p className="field-hint tone-text-pending">A data informada é posterior a hoje.</p>}
          {ultrapassa && (
            <p className="field-hint tone-text-pending">
              Com este lançamento, {form.etapa} ficará com{' '}
              {formatarNumero(jaLancadoEtapa + qtdDigitada)} pares, acima dos{' '}
              {formatarNumero(remessaSelecionada.quantidade_pares)} planejados.
            </p>
          )}

          {erroForm && <Alert tipo="error">{erroForm}</Alert>}

          <button id="registro-enviar" type="submit" className="btn btn-primary btn-block" disabled={enviando}>
            {enviando ? <span className="spinner" /> : <Icon name="check" size={16} />}
            {enviando ? 'Registrando...' : 'Registrar lançamento'}
          </button>

          {remessasAtivas.length === 0 && !carregando && (
            <p className="field-hint">Não há remessas ativas para receber lançamentos.</p>
          )}
        </form>
      </Card>

      {/* ---------- Consulta ---------- */}
      <div className="stack">
        <div className="stage-totals">
          {ETAPAS.map((etapa) => (
            <article key={etapa} className={`stage-total stage-${chaveEtapa[etapa]}`}>
              <StageIcon etapa={etapa} />
              <div>
                <span>{etapa}</span>
                <strong className="num">{carregando && lancamentos.length === 0 ? '…' : formatarNumero(totais[etapa])}</strong>
                <small>pares nos filtros</small>
              </div>
            </article>
          ))}
          <article className="stage-total stage-neutral">
            <span className="stage-icon">
              <Icon name="clipboard" size={18} />
            </span>
            <div>
              <span>Lançamentos</span>
              <strong className="num">{carregando && lancamentos.length === 0 ? '…' : filtrados.length}</strong>
              <small>{filtrosAtivos ? `de ${lancamentos.length} no total` : 'registros individuais'}</small>
            </div>
          </article>
        </div>

        <Card
          titulo="Lançamentos registrados"
          subtitulo="Apontamentos individuais gravados no banco — não é o resumo agregado."
          icone="activity"
          semPadding
          acoes={
            <button id="producao-atualizar" type="button" className="btn btn-secondary btn-sm" onClick={onAtualizar} disabled={carregando}>
              <Icon name="refresh" size={14} />
              {carregando ? 'Carregando...' : 'Atualizar'}
            </button>
          }
        >
          <div className="filter-row">
            <label className="field field-compact">
              <span className="field-label">De</span>
              <input id="producao-filtro-inicio" className="input" type="date" name="dataInicio" value={filtros.dataInicio} onChange={alterarFiltro} />
            </label>
            <label className="field field-compact">
              <span className="field-label">Até</span>
              <input id="producao-filtro-fim" className="input" type="date" name="dataFim" value={filtros.dataFim} onChange={alterarFiltro} />
            </label>
            <label className="field field-compact">
              <span className="field-label">Remessa</span>
              <select id="producao-filtro-remessa" className="input" name="remessaId" value={filtros.remessaId} onChange={alterarFiltro}>
                <option value="">Todas</option>
                {opcoesRemessa.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.numero}
                    {r.status === STATUS_EXCLUIDA ? ' (excluída)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="field field-compact">
              <span className="field-label">Etapa</span>
              <select id="producao-filtro-etapa" className="input" name="etapa" value={filtros.etapa} onChange={alterarFiltro}>
                <option value="">Todas</option>
                {ETAPAS.map((etapa) => (
                  <option key={etapa} value={etapa}>
                    {etapa}
                  </option>
                ))}
              </select>
            </label>
            <div className="filter-row-actions">
              <button
                id="producao-filtro-hoje"
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setFiltros((a) => ({ ...a, dataInicio: hojeISO(), dataFim: hojeISO() }))}
              >
                Hoje
              </button>
              <button
                id="producao-filtro-limpar"
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setFiltros(filtrosVazios)}
                disabled={!filtrosAtivos}
              >
                Limpar
              </button>
            </div>
          </div>

          {periodoInvalido && (
            <div className="card-alert">
              <Alert tipo="error">A data inicial não pode ser posterior à data final.</Alert>
            </div>
          )}

          {erro && (
            <div className="card-alert">
              <Alert
                tipo="error"
                acao={
                  <button type="button" className="btn btn-secondary btn-sm" onClick={onAtualizar}>
                    Tentar novamente
                  </button>
                }
              >
                {erro}
              </Alert>
            </div>
          )}

          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Remessa</th>
                  <th>Etapa</th>
                  <th className="num">Pares</th>
                  <th>Observação</th>
                  <th>Registrado em</th>
                </tr>
              </thead>
              <tbody>
                {carregando && lancamentos.length === 0 && <SkeletonRows colunas={6} />}
                {!erro &&
                  filtrados.map((l) => (
                    <tr key={l.id}>
                      <td className="strong">{formatarData(l.data_producao)}</td>
                      <td>
                        <div className="cell-main">
                          <span className="code">
                            #{l.remessa_numero}
                            {l.status_remessa === STATUS_EXCLUIDA && <span className="badge badge-deleted badge-xs">Excluída</span>}
                          </span>
                          <span className="cell-sub">{l.remessa_descricao || '—'}</span>
                        </div>
                      </td>
                      <td>
                        <StageTag etapa={l.etapa} />
                      </td>
                      <td className="num strong">{formatarNumero(l.quantidade_pares)}</td>
                      <td className="cell-wrap">{l.observacao || <span className="text-faint">—</span>}</td>
                      <td className="muted-text">{formatarDataHora(l.registrado_em)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {!carregando && !erro && filtrados.length === 0 && (
            <EmptyState icone="activity" titulo={lancamentos.length === 0 ? 'Nenhum lançamento registrado' : 'Nenhum resultado'}>
              {lancamentos.length === 0
                ? 'Use o formulário ao lado para registrar a primeira produção.'
                : 'Nenhum lançamento corresponde aos filtros selecionados.'}
            </EmptyState>
          )}

          <footer className="card-foot">
            <span>Totais por etapa calculados somente sobre os lançamentos exibidos.</span>
          </footer>
        </Card>
      </div>
    </div>
  )
}

export default ProducaoDiaria

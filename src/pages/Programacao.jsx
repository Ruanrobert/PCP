import { useMemo, useState } from 'react'
import Icon from '../components/Icon'
import { Badge, Card, EmptyState, Kpi, SkeletonRows, StageValue, StatusBadge } from '../components/ui'
import {
  STATUS_EXCLUIDA,
  STATUS_REMESSA,
  descreverPrazo,
  diasAteEntrega,
  formatarData,
  formatarMesAno,
  formatarNumero,
  hojeISO,
} from '../pcpUtils'

const COLUNAS = 10

function diaSemana(dataISO) {
  if (!dataISO) return ''
  const d = new Date(`${dataISO}T12:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '')
}

function Programacao({ remessas, resumoPorRemessa, carregando, onEditar, onVisualizar }) {
  const [busca, setBusca] = useState('')
  const [status, setStatus] = useState('')
  const [prazo, setPrazo] = useState('')
  const [ordemAsc, setOrdemAsc] = useState(true)
  const [mostrarExcluidas, setMostrarExcluidas] = useState(false)

  const hoje = hojeISO()

  // Mesma regra de exclusão lógica usada no restante do sistema.
  const ativas = useMemo(() => remessas.filter((r) => r.status !== STATUS_EXCLUIDA), [remessas])
  const excluidas = useMemo(() => remessas.filter((r) => r.status === STATUS_EXCLUIDA), [remessas])

  const opcoesStatus = useMemo(() => {
    const conjunto = new Set(STATUS_REMESSA)
    ativas.forEach((r) => conjunto.add(r.status))
    return [...conjunto]
  }, [ativas])

  const linhas = useMemo(() => {
    const termo = busca.trim().toLowerCase()
    const lista = ativas
      .map((r) => ({ ...r, dias: diasAteEntrega(r.data_entrega, hoje) }))
      .filter((r) => `${r.numero} ${r.descricao}`.toLowerCase().includes(termo))
      .filter((r) => !status || r.status === status)
      .filter((r) => {
        if (!prazo) return true
        if (prazo === 'sem-data') return r.dias === null
        if (r.dias === null || r.status === 'Concluída') return false
        if (prazo === 'vencidas') return r.dias < 0
        if (prazo === '7') return r.dias >= 0 && r.dias <= 7
        if (prazo === '30') return r.dias >= 0 && r.dias <= 30
        return true
      })

    // Ordena pela data de entrega; remessas sem data ficam sempre no final.
    return lista.sort((a, b) => {
      if (!a.data_entrega && !b.data_entrega) return String(a.numero).localeCompare(String(b.numero))
      if (!a.data_entrega) return 1
      if (!b.data_entrega) return -1
      const comparacao = a.data_entrega.localeCompare(b.data_entrega)
      if (comparacao !== 0) return ordemAsc ? comparacao : -comparacao
      return String(a.numero).localeCompare(String(b.numero))
    })
  }, [ativas, busca, status, prazo, ordemAsc, hoje])

  // Agrupa as linhas já ordenadas por mês de entrega.
  const grupos = useMemo(() => {
    const resultado = []
    linhas.forEach((r) => {
      const chave = r.data_entrega ? r.data_entrega.slice(0, 7) : 'sem-data'
      let grupo = resultado[resultado.length - 1]
      if (!grupo || grupo.chave !== chave) {
        grupo = { chave, titulo: formatarMesAno(r.data_entrega), itens: [], pares: 0 }
        resultado.push(grupo)
      }
      grupo.itens.push(r)
      grupo.pares += Number(r.quantidade_pares || 0)
    })
    return resultado
  }, [linhas])

  const totalPlanejado = linhas.reduce((t, r) => t + Number(r.quantidade_pares || 0), 0)
  const totalMontado = linhas.reduce((t, r) => t + Number(resumoPorRemessa.get(Number(r.id))?.montagem || 0), 0)
  const vencidas = ativas.filter((r) => {
    const dias = diasAteEntrega(r.data_entrega, hoje)
    return dias !== null && dias < 0 && r.status !== 'Concluída'
  }).length
  const semData = ativas.filter((r) => !r.data_entrega).length
  const filtrosAtivos = Boolean(busca || status || prazo)

  return (
    <div className="stack">
      <div className="kpi-grid">
        <Kpi
          icone="calendar"
          rotulo="Remessas na programação"
          valor={linhas.length}
          rodape={filtrosAtivos ? `de ${ativas.length} remessa(s) ativa(s)` : 'Remessas ativas'}
          carregando={carregando && ativas.length === 0}
        />
        <Kpi
          icone="layers"
          tom="costura"
          rotulo="Pares planejados"
          valor={formatarNumero(totalPlanejado)}
          rodape={`Montados: ${formatarNumero(totalMontado)}`}
          carregando={carregando && ativas.length === 0}
        />
        <Kpi
          icone="alert"
          tom={vencidas > 0 ? 'danger' : 'warning'}
          rotulo="Entregas vencidas"
          valor={vencidas}
          rodape="Entrega anterior a hoje, não concluídas"
          destaque={vencidas > 0 ? 'danger' : undefined}
          carregando={carregando && ativas.length === 0}
        />
        <Kpi
          icone="clock"
          tom="neutral"
          rotulo="Sem data de entrega"
          valor={semData}
          rodape="Remessas ativas sem data definida"
          carregando={carregando && ativas.length === 0}
        />
      </div>

      <section className="card">
        <div className="toolbar">
          <div className="toolbar-filters toolbar-filters-start">
            <label className="search-field search-field-wide">
              <Icon name="search" size={16} />
              <input
                id="programacao-busca"
                value={busca}
                onChange={(e) => setBusca(e.target.value)}
                placeholder="Número da remessa ou cliente..."
                aria-label="Pesquisar na programação"
              />
            </label>
            <select id="programacao-status" className="input input-compact" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
              <option value="">Todos os status</option>
              {opcoesStatus.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select id="programacao-prazo" className="input input-compact" value={prazo} onChange={(e) => setPrazo(e.target.value)} aria-label="Entrega">
              <option value="">Todas as datas</option>
              <option value="vencidas">Vencidas</option>
              <option value="7">Próximos 7 dias</option>
              <option value="30">Próximos 30 dias</option>
              <option value="sem-data">Sem data</option>
            </select>
            {filtrosAtivos && (
              <button
                id="programacao-limpar"
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setBusca('')
                  setStatus('')
                  setPrazo('')
                }}
              >
                Limpar filtros
              </button>
            )}
          </div>
          <button id="programacao-ordem" type="button" className="btn btn-secondary btn-sm" onClick={() => setOrdemAsc((a) => !a)}>
            <Icon name="sort" size={14} />
            {ordemAsc ? 'Entrega mais próxima primeiro' : 'Entrega mais distante primeiro'}
          </button>
        </div>

        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Entrega</th>
                <th>Prazo</th>
                <th>Remessa</th>
                <th className="num">Pares</th>
                <th>Corte</th>
                <th>Costura</th>
                <th>Montagem</th>
                <th className="num">Saldo a montar</th>
                <th>Status</th>
                <th className="col-actions">Ações</th>
              </tr>
            </thead>
            <tbody>
              {carregando && ativas.length === 0 && <SkeletonRows colunas={COLUNAS} />}
              {grupos.map((grupo) => (
                <GrupoMes key={grupo.chave} grupo={grupo} resumoPorRemessa={resumoPorRemessa} onEditar={onEditar} onVisualizar={onVisualizar} />
              ))}
            </tbody>
          </table>
        </div>

        {!carregando && linhas.length === 0 && (
          <EmptyState icone="calendar" titulo={ativas.length === 0 ? 'Nada para programar' : 'Nenhum resultado'}>
            {ativas.length === 0
              ? 'Nenhuma remessa ativa cadastrada.'
              : 'Nenhuma remessa corresponde aos filtros selecionados.'}
          </EmptyState>
        )}

        <footer className="card-foot">
          <span>
            Prazo em dias corridos até a data de entrega cadastrada. Saldo a montar = pares planejados − pares lançados na
            Montagem. Sem cálculo de capacidade ou sequenciamento automático.
          </span>
        </footer>
      </section>

      {excluidas.length > 0 && (
        <Card
          titulo="Remessas excluídas"
          subtitulo="Fora da programação — exibidas apenas para consulta do histórico."
          icone="archive"
          semPadding
          className="card-muted"
          acoes={
            <button id="programacao-toggle-excluidas" type="button" className="btn btn-ghost btn-sm" onClick={() => setMostrarExcluidas((a) => !a)}>
              {mostrarExcluidas ? 'Ocultar' : `Mostrar (${excluidas.length})`}
            </button>
          }
        >
          {mostrarExcluidas && (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Remessa</th>
                    <th>Entrega</th>
                    <th className="num">Pares</th>
                    <th>Status</th>
                    <th className="col-actions">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {excluidas.map((r) => (
                    <tr key={r.id} className="row-muted">
                      <td>
                        <div className="cell-main">
                          <span className="code">#{r.numero}</span>
                          <span className="cell-sub">{r.descricao}</span>
                        </div>
                      </td>
                      <td>{formatarData(r.data_entrega)}</td>
                      <td className="num">{formatarNumero(r.quantidade_pares)}</td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                      <td className="col-actions">
                        <div className="row-actions">
                          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onVisualizar(r)}>
                            <Icon name="history" size={14} />
                            Histórico
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

function GrupoMes({ grupo, resumoPorRemessa, onEditar, onVisualizar }) {
  return (
    <>
      <tr className="group-row">
        <td colSpan={COLUNAS}>
          <span className="group-title">{grupo.titulo}</span>
          <span className="group-meta">
            {grupo.itens.length} remessa(s) · {formatarNumero(grupo.pares)} pares
          </span>
        </td>
      </tr>
      {grupo.itens.map((r) => {
        const producao = resumoPorRemessa.get(Number(r.id)) || {}
        const infoPrazo = descreverPrazo(r.dias, r.status)
        const saldo = Math.max(0, Number(r.quantidade_pares || 0) - Number(producao.montagem || 0))
        return (
          <tr key={r.id}>
            <td>
              <div className="date-cell">
                <strong>{formatarData(r.data_entrega)}</strong>
                <small>{diaSemana(r.data_entrega)}</small>
              </div>
            </td>
            <td>
              <Badge variante={infoPrazo.variante}>{infoPrazo.texto}</Badge>
            </td>
            <td>
              <div className="cell-main">
                <span className="code">#{r.numero}</span>
                <span className="cell-sub">{r.descricao}</span>
              </div>
            </td>
            <td className="num strong">{formatarNumero(r.quantidade_pares)}</td>
            <td>
              <StageValue valor={producao.corte} total={r.quantidade_pares} etapa="Corte" />
            </td>
            <td>
              <StageValue valor={producao.costura} total={r.quantidade_pares} etapa="Costura" />
            </td>
            <td>
              <StageValue valor={producao.montagem} total={r.quantidade_pares} etapa="Montagem" />
            </td>
            <td className="num">{formatarNumero(saldo)}</td>
            <td>
              <StatusBadge status={r.status} />
            </td>
            <td className="col-actions">
              <div className="row-actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => onVisualizar(r)}
                  title="Visualizar detalhes e histórico"
                  aria-label={`Visualizar remessa ${r.numero}`}
                >
                  <Icon name="eye" size={17} />
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => onEditar(r)}
                  title="Editar remessa"
                  aria-label={`Editar remessa ${r.numero}`}
                >
                  <Icon name="edit" size={16} />
                </button>
              </div>
            </td>
          </tr>
        )
      })}
    </>
  )
}

export default Programacao

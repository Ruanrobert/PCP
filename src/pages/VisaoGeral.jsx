import { useMemo } from 'react'
import Icon from '../components/Icon'
import { Badge, Card, EmptyState, Kpi, ProgressBar, SkeletonRows, StageIcon, StageTag, StatusBadge } from '../components/ui'
import {
  ETAPAS,
  STATUS_REMESSA,
  descreverPrazo,
  diasAteEntrega,
  formatarData,
  formatarNumero,
  percentual,
  varianteStatus,
} from '../pcpUtils'

const chaveEtapa = { Corte: 'corte', Costura: 'costura', Montagem: 'montagem' }

function VisaoGeral({
  remessasAtivas,
  remessasExcluidas,
  resumoProducao,
  resumoPorRemessa,
  lancamentos,
  carregando,
  carregandoResumo,
  carregandoLancamentos,
  onNavegar,
  onVisualizar,
}) {
  const totalPares = remessasAtivas.reduce((t, r) => t + Number(r.quantidade_pares || 0), 0)

  // Totais por etapa a partir de GET /api/producao/resumo (já exclui remessas excluídas).
  const totaisEtapa = useMemo(() => {
    const base = { Corte: 0, Costura: 0, Montagem: 0 }
    resumoProducao.forEach((item) => {
      ETAPAS.forEach((etapa) => {
        base[etapa] += Number(item[chaveEtapa[etapa]] || 0)
      })
    })
    return base
  }, [resumoProducao])

  const contagemStatus = useMemo(() => {
    const mapa = new Map(STATUS_REMESSA.map((s) => [s, 0]))
    remessasAtivas.forEach((r) => mapa.set(r.status, (mapa.get(r.status) || 0) + 1))
    return [...mapa.entries()]
  }, [remessasAtivas])

  const prazos = useMemo(() => {
    let vencidas = 0
    let proximos7 = 0
    remessasAtivas.forEach((r) => {
      if (r.status === 'Concluída') return
      const dias = diasAteEntrega(r.data_entrega)
      if (dias === null) return
      if (dias < 0) vencidas += 1
      else if (dias <= 7) proximos7 += 1
    })
    return { vencidas, proximos7 }
  }, [remessasAtivas])

  const proximasEntregas = useMemo(
    () =>
      remessasAtivas
        .filter((r) => r.data_entrega && r.status !== 'Concluída')
        .sort((a, b) => a.data_entrega.localeCompare(b.data_entrega))
        .slice(0, 5),
    [remessasAtivas],
  )

  const recentes = lancamentos.slice(0, 6)
  const aProgramar = remessasAtivas.filter((r) => r.status === 'A programar').length
  const emAndamento = remessasAtivas.filter((r) => r.status === 'Em andamento').length
  const pctMontado = percentual(totaisEtapa.Montagem, totalPares)

  return (
    <div className="stack">
      <div className="kpi-grid">
        <Kpi
          icone="clipboard"
          tom="primary"
          rotulo="Remessas ativas"
          valor={remessasAtivas.length}
          rodape={`${aProgramar} a programar · ${emAndamento} em andamento`}
          carregando={carregando && remessasAtivas.length === 0}
        />
        <Kpi
          icone="layers"
          tom="costura"
          rotulo="Pares planejados"
          valor={formatarNumero(totalPares)}
          rodape="Soma das remessas ativas"
          carregando={carregando && remessasAtivas.length === 0}
        />
        <Kpi
          icone="box"
          tom="montagem"
          rotulo="Pares montados"
          valor={formatarNumero(totaisEtapa.Montagem)}
          rodape={`${pctMontado}% do total planejado`}
          carregando={carregandoResumo && resumoProducao.length === 0}
        />
        <Kpi
          icone="flag"
          tom={prazos.vencidas > 0 ? 'danger' : 'warning'}
          rotulo="Entregas em 7 dias"
          valor={prazos.proximos7}
          rodape={prazos.vencidas > 0 ? `${prazos.vencidas} entrega(s) vencida(s)` : 'Nenhuma entrega vencida'}
          destaque={prazos.vencidas > 0 ? 'danger' : undefined}
          carregando={carregando && remessasAtivas.length === 0}
        />
      </div>

      <div className="grid-2-1">
        <Card
          titulo="Fluxo de produção por etapa"
          subtitulo="Pares registrados em cada etapa em relação ao total planejado das remessas ativas"
          icone="activity"
          acoes={
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onNavegar('Produção diária')}>
              Produção diária
              <Icon name="arrowRight" size={14} />
            </button>
          }
        >
          <div className="flow">
            {ETAPAS.map((etapa, i) => {
              const valor = totaisEtapa[etapa]
              const pct = percentual(valor, totalPares)
              return (
                <div className="flow-step" key={etapa}>
                  <div className="flow-head">
                    <StageIcon etapa={etapa} />
                    <div>
                      <span className="flow-index">Etapa {i + 1}</span>
                      <strong>{etapa}</strong>
                    </div>
                    <span className="flow-pct num">{pct}%</span>
                  </div>
                  <div className="flow-value num">
                    {carregandoResumo && resumoProducao.length === 0 ? (
                      <span className="skeleton skeleton-kpi" />
                    ) : (
                      <>
                        <strong>{formatarNumero(valor)}</strong>
                        <span> / {formatarNumero(totalPares)} pares</span>
                      </>
                    )}
                  </div>
                  <ProgressBar valor={pct} tom={chaveEtapa[etapa]} rotulo={`${etapa}: ${pct}%`} />
                </div>
              )
            })}
          </div>
        </Card>

        <Card titulo="Situação das remessas" subtitulo="Distribuição por status" icone="clipboard">
          <ul className="status-list">
            {contagemStatus.map(([status, qtd]) => (
              <li key={status}>
                <span className={`status-dot dot-${varianteStatus(status)}`} />
                <span className="status-name">{status}</span>
                <span className="status-bar">
                  <ProgressBar
                    valor={percentual(qtd, remessasAtivas.length)}
                    tom={varianteStatus(status)}
                    rotulo={`${status}: ${qtd}`}
                  />
                </span>
                <strong className="num">{qtd}</strong>
              </li>
            ))}
            <li className="status-list-sep">
              <span className="status-dot dot-deleted" />
              <span className="status-name">Excluídas</span>
              <span className="status-bar muted-text">fora dos totais</span>
              <strong className="num">{remessasExcluidas.length}</strong>
            </li>
          </ul>
        </Card>
      </div>

      <div className="grid-2-1">
        <Card
          titulo="Próximas entregas"
          subtitulo="Remessas ativas mais próximas da data de entrega (exceto concluídas)"
          icone="calendar"
          semPadding
          acoes={
            <button
              id="visao-abrir-programacao"
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onNavegar('Programação')}
            >
              Ver programação
              <Icon name="arrowRight" size={14} />
            </button>
          }
        >
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Remessa</th>
                  <th>Entrega</th>
                  <th className="num">Pares</th>
                  <th>Montagem</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {carregando && proximasEntregas.length === 0 && <SkeletonRows colunas={5} />}
                {proximasEntregas.map((r) => {
                  const montado = Number(resumoPorRemessa.get(Number(r.id))?.montagem || 0)
                  const pct = percentual(montado, r.quantidade_pares)
                  const prazo = descreverPrazo(diasAteEntrega(r.data_entrega), r.status)
                  return (
                    <tr key={r.id} className="row-clickable" onClick={() => onVisualizar(r)}>
                      <td>
                        <div className="cell-main">
                          <span className="code">#{r.numero}</span>
                          <span className="cell-sub">{r.descricao}</span>
                        </div>
                      </td>
                      <td>
                        <div className="cell-main">
                          <span>{formatarData(r.data_entrega)}</span>
                          <Badge variante={prazo.variante}>{prazo.texto}</Badge>
                        </div>
                      </td>
                      <td className="num">{formatarNumero(r.quantidade_pares)}</td>
                      <td>
                        <div className="inline-progress">
                          <ProgressBar valor={pct} tom="montagem" rotulo={`Montagem ${pct}%`} />
                          <span className="num">{pct}%</span>
                        </div>
                      </td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {!carregando && proximasEntregas.length === 0 && (
            <EmptyState icone="calendar" titulo="Nenhuma entrega pendente">
              Não há remessas ativas com data de entrega definida.
            </EmptyState>
          )}
        </Card>

        <Card
          titulo="Atividade recente"
          subtitulo="Últimos lançamentos de produção"
          icone="clock"
          acoes={
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onNavegar('Produção diária')}>
              Ver todos
              <Icon name="arrowRight" size={14} />
            </button>
          }
        >
          {carregandoLancamentos && lancamentos.length === 0 ? (
            <div className="timeline-loading">
              <span className="skeleton" />
              <span className="skeleton" />
              <span className="skeleton" />
            </div>
          ) : recentes.length === 0 ? (
            <EmptyState icone="activity" titulo="Sem lançamentos">
              Nenhuma produção registrada até o momento.
            </EmptyState>
          ) : (
            <ul className="activity-list">
              {recentes.map((l) => (
                <li key={l.id}>
                  <StageTag etapa={l.etapa} />
                  <div className="activity-main">
                    <span>
                      <strong className="num">{formatarNumero(l.quantidade_pares)}</strong> pares ·{' '}
                      <span className="code">#{l.remessa_numero}</span>
                    </span>
                    <small>{formatarData(l.data_producao)}</small>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

export default VisaoGeral

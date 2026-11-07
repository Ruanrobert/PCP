import Icon from './Icon'
import { Alert, EmptyState, StageIcon, StageTag, StatusBadge, ProgressBar } from './ui'
import { useEscape } from './useEscape'
import {
  ETAPAS,
  STATUS_EXCLUIDA,
  descreverPrazo,
  diasAteEntrega,
  formatarData,
  formatarDataHora,
  formatarNumero,
  percentual,
} from '../pcpUtils'

const rotuloAcao = {
  CRIACAO: { texto: 'Criação', icone: 'plus', tom: 'success' },
  EDICAO: { texto: 'Edição', icone: 'edit', tom: 'primary' },
  EXCLUSAO: { texto: 'Exclusão', icone: 'trash', tom: 'danger' },
  PRODUCAO: { texto: 'Produção', icone: 'activity', tom: 'montagem' },
  EXCLUSAO_PRODUCAO: { texto: 'Lançamento removido', icone: 'trash', tom: 'danger' },
}

const chaveEtapa = { Corte: 'corte', Costura: 'costura', Montagem: 'montagem' }

// Painel lateral com os dados da remessa, produção por etapa, lançamentos e histórico
// (GET /api/remessas/{id}/historico — mesmo endpoint e tratamento de erro de antes).
function RemessaDrawer({
  remessa,
  producao,
  lancamentos,
  historico,
  carregando,
  erro,
  onRecarregar,
  onFechar,
  onEditar,
}) {
  useEscape(true, () => {
    if (!carregando) onFechar()
  })

  const excluida = remessa.status === STATUS_EXCLUIDA
  const prazo = descreverPrazo(diasAteEntrega(remessa.data_entrega), remessa.status)

  return (
    <div
      className="overlay overlay-drawer"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !carregando) onFechar()
      }}
    >
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="titulo-drawer">
        <header className="drawer-head">
          <div>
            <span className="overline">Remessa</span>
            <h2 id="titulo-drawer" className="code-title">
              #{remessa.numero}
            </h2>
            <p>{remessa.descricao}</p>
          </div>
          <button type="button" className="icon-btn" onClick={onFechar} disabled={carregando} aria-label="Fechar detalhes">
            <Icon name="close" size={18} />
          </button>
        </header>

        <div className="drawer-body">
          {excluida && (
            <Alert tipo="info" titulo="Remessa excluída">
              Registro preservado no banco apenas para consulta. Não entra nos totais nem na programação.
            </Alert>
          )}

          <dl className="detail-grid">
            <div>
              <dt>Status</dt>
              <dd>
                <StatusBadge status={remessa.status} />
              </dd>
            </div>
            <div>
              <dt>Entrega</dt>
              <dd>
                {formatarData(remessa.data_entrega)}
                {!excluida && remessa.data_entrega && <small className={`prazo-text tone-text-${prazo.variante}`}>{prazo.texto}</small>}
              </dd>
            </div>
            <div>
              <dt>Pares planejados</dt>
              <dd className="num strong">{formatarNumero(remessa.quantidade_pares)}</dd>
            </div>
            <div>
              <dt>Criada em</dt>
              <dd>{formatarDataHora(remessa.criado_em)}</dd>
            </div>
            <div>
              <dt>Última atualização</dt>
              <dd>{formatarDataHora(remessa.atualizado_em)}</dd>
            </div>
            <div>
              <dt>ID interno</dt>
              <dd className="num">{remessa.id}</dd>
            </div>
          </dl>

          <section className="drawer-section">
            <h3>Produção por etapa</h3>
            {excluida && !producao ? (
              <p className="muted-text">O resumo de produção considera apenas remessas ativas.</p>
            ) : (
              <div className="stage-summary">
                {ETAPAS.map((etapa) => {
                  const valor = Number(producao?.[chaveEtapa[etapa]] || 0)
                  const pct = percentual(valor, remessa.quantidade_pares)
                  return (
                    <div key={etapa} className="stage-summary-row">
                      <StageIcon etapa={etapa} size={16} />
                      <div className="stage-summary-main">
                        <div className="stage-summary-top">
                          <span>{etapa}</span>
                          <span className="num">
                            <strong>{formatarNumero(valor)}</strong> / {formatarNumero(remessa.quantidade_pares)}
                          </span>
                        </div>
                        <ProgressBar valor={pct} tom={chaveEtapa[etapa]} rotulo={`${etapa} ${pct}%`} />
                      </div>
                      <span className="stage-summary-pct num">{pct}%</span>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <section className="drawer-section">
            <h3>
              Lançamentos de produção <span className="count-pill">{lancamentos.length}</span>
            </h3>
            {lancamentos.length === 0 ? (
              <p className="muted-text">Nenhum lançamento registrado para esta remessa.</p>
            ) : (
              <ul className="mini-list">
                {lancamentos.slice(0, 8).map((l) => (
                  <li key={l.id}>
                    <StageTag etapa={l.etapa} />
                    <span className="num strong">{formatarNumero(l.quantidade_pares)} pares</span>
                    <span className="muted-text">{formatarData(l.data_producao)}</span>
                  </li>
                ))}
                {lancamentos.length > 8 && (
                  <li className="muted-text">+ {lancamentos.length - 8} lançamento(s) na Produção diária</li>
                )}
              </ul>
            )}
          </section>

          <section className="drawer-section">
            <div className="drawer-section-head">
              <h3>Histórico</h3>
              <button type="button" className="btn btn-ghost btn-sm" onClick={onRecarregar} disabled={carregando}>
                <Icon name="refresh" size={14} />
                {carregando ? 'Atualizando...' : 'Atualizar histórico'}
              </button>
            </div>

            {carregando && (
              <div className="timeline-loading">
                <span className="skeleton" />
                <span className="skeleton" />
                <span className="skeleton" />
              </div>
            )}

            {!carregando && erro && (
              <Alert
                tipo="error"
                acao={
                  <button type="button" className="btn btn-secondary btn-sm" onClick={onRecarregar}>
                    Tentar novamente
                  </button>
                }
              >
                {erro}
              </Alert>
            )}

            {!carregando && !erro && historico.length === 0 && (
              <EmptyState icone="history" titulo="Sem histórico">
                Nenhum registro de histórico encontrado para esta remessa.
              </EmptyState>
            )}

            {!carregando && !erro && historico.length > 0 && (
              <ol className="timeline">
                {historico.map((item) => {
                  const info = rotuloAcao[String(item.acao || '').toUpperCase()] || {
                    texto: item.acao,
                    icone: 'info',
                    tom: 'neutral',
                  }
                  return (
                    <li key={item.id} className="timeline-item">
                      <span className={`timeline-dot tone-${info.tom}`}>
                        <Icon name={info.icone} size={13} strokeWidth={2.2} />
                      </span>
                      <div className="timeline-content">
                        <div className="timeline-top">
                          <strong title={item.acao}>{info.texto}</strong>
                          <time>{formatarDataHora(item.registrado_em || item.criado_em)}</time>
                        </div>
                        <p>{item.detalhes}</p>
                      </div>
                    </li>
                  )
                })}
              </ol>
            )}
          </section>
        </div>

        <footer className="drawer-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar} disabled={carregando}>
            Fechar
          </button>
          {onEditar && !excluida && (
            <button type="button" className="btn btn-primary" onClick={() => onEditar(remessa)}>
              <Icon name="edit" size={16} />
              Editar remessa
            </button>
          )}
        </footer>
      </aside>
    </div>
  )
}

export default RemessaDrawer

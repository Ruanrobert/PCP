import { useMemo, useState } from 'react'
import Icon from '../components/Icon'
import { Badge, EmptyState, SkeletonRows, StageValue, StatusBadge } from '../components/ui'
import {
  STATUS_REMESSA,
  descreverPrazo,
  diasAteEntrega,
  formatarData,
  formatarDataHora,
  formatarNumero,
} from '../pcpUtils'

function QuadroRemessas({
  remessasAtivas,
  remessasExcluidas,
  resumoPorRemessa,
  carregando,
  onNova,
  onVisualizar,
  onEditar,
  onExcluir,
}) {
  const [aba, setAba] = useState('ativas')
  const [busca, setBusca] = useState('')
  const [status, setStatus] = useState('')

  const termo = busca.trim().toLowerCase()

  const ativasFiltradas = useMemo(
    () =>
      remessasAtivas.filter(
        (r) => `${r.numero} ${r.descricao}`.toLowerCase().includes(termo) && (!status || r.status === status),
      ),
    [remessasAtivas, termo, status],
  )

  const excluidasFiltradas = useMemo(
    () => remessasExcluidas.filter((r) => `${r.numero} ${r.descricao}`.toLowerCase().includes(termo)),
    [remessasExcluidas, termo],
  )

  const lista = aba === 'ativas' ? ativasFiltradas : excluidasFiltradas
  const total = aba === 'ativas' ? remessasAtivas.length : remessasExcluidas.length

  return (
    <section className="card">
      <div className="toolbar">
        <div className="segmented" role="tablist" aria-label="Situação das remessas">
          <button
            id="quadro-aba-ativas"
            role="tab"
            aria-selected={aba === 'ativas'}
            className={aba === 'ativas' ? 'active' : ''}
            onClick={() => setAba('ativas')}
          >
            Ativas <span className="count-pill">{remessasAtivas.length}</span>
          </button>
          <button
            id="quadro-aba-excluidas"
            role="tab"
            aria-selected={aba === 'excluidas'}
            className={aba === 'excluidas' ? 'active' : ''}
            onClick={() => setAba('excluidas')}
          >
            Excluídas <span className="count-pill">{remessasExcluidas.length}</span>
          </button>
        </div>

        <div className="toolbar-filters">
          <label className="search-field">
            <Icon name="search" size={16} />
            <input
              id="quadro-busca"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              placeholder="Buscar remessa ou descrição..."
              aria-label="Buscar remessa ou descrição"
            />
          </label>
          {aba === 'ativas' && (
            <select
              id="quadro-status"
              className="input input-compact"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Filtrar por status"
            >
              <option value="">Todos os status</option>
              {STATUS_REMESSA.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {aba === 'excluidas' && (
        <div className="band band-danger">
          <Icon name="archive" size={16} />
          Remessas excluídas são preservadas no banco (exclusão lógica). Ficam disponíveis apenas para consulta do
          histórico e não entram nos totais.
        </div>
      )}

      <div className="table-scroll">
        {aba === 'ativas' ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>Remessa</th>
                <th>Entrega</th>
                <th className="num">Pares</th>
                <th>Corte</th>
                <th>Costura</th>
                <th>Montagem</th>
                <th>Status</th>
                <th className="col-actions">Ações</th>
              </tr>
            </thead>
            <tbody>
              {carregando && remessasAtivas.length === 0 && <SkeletonRows colunas={8} />}
              {ativasFiltradas.map((r) => {
                const producao = resumoPorRemessa.get(Number(r.id)) || {}
                const prazo = descreverPrazo(diasAteEntrega(r.data_entrega), r.status)
                return (
                  <tr key={r.id}>
                    <td>
                      <div className="cell-main">
                        <span className="code">#{r.numero}</span>
                        <span className="cell-sub">{r.descricao}</span>
                      </div>
                    </td>
                    <td>
                      <div className="cell-main">
                        <span>{formatarData(r.data_entrega)}</span>
                        {r.data_entrega && <Badge variante={prazo.variante}>{prazo.texto}</Badge>}
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
                        <button
                          type="button"
                          className="icon-btn icon-btn-danger"
                          onClick={() => onExcluir(r)}
                          title="Excluir remessa"
                          aria-label={`Excluir remessa ${r.numero}`}
                        >
                          <Icon name="trash" size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Remessa</th>
                <th>Entrega</th>
                <th className="num">Pares</th>
                <th>Última atualização</th>
                <th>Status</th>
                <th className="col-actions">Ações</th>
              </tr>
            </thead>
            <tbody>
              {excluidasFiltradas.map((r) => (
                <tr key={r.id} className="row-muted">
                  <td>
                    <div className="cell-main">
                      <span className="code">#{r.numero}</span>
                      <span className="cell-sub">{r.descricao}</span>
                    </div>
                  </td>
                  <td>{formatarData(r.data_entrega)}</td>
                  <td className="num">{formatarNumero(r.quantidade_pares)}</td>
                  <td>{formatarDataHora(r.atualizado_em)}</td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="col-actions">
                    <div className="row-actions">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onVisualizar(r)}
                        aria-label={`Histórico da remessa ${r.numero}`}
                      >
                        <Icon name="history" size={14} />
                        Histórico
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!carregando && lista.length === 0 && (
        <EmptyState
          icone={aba === 'ativas' ? 'clipboard' : 'archive'}
          titulo={
            total === 0
              ? aba === 'ativas'
                ? 'Nenhuma remessa cadastrada'
                : 'Nenhuma remessa excluída'
              : 'Nenhuma remessa encontrada'
          }
          acao={
            aba === 'ativas' && total === 0 ? (
              <button type="button" className="btn btn-primary btn-sm" onClick={onNova}>
                <Icon name="plus" size={14} />
                Nova remessa
              </button>
            ) : null
          }
        >
          {total === 0 ? null : 'Nenhuma remessa corresponde à busca ou ao filtro selecionado.'}
        </EmptyState>
      )}

      <footer className="card-foot">
        <span>
          Exibindo <strong>{lista.length}</strong> de <strong>{total}</strong> remessa(s)
          {aba === 'ativas' ? ' ativa(s)' : ' excluída(s)'}
        </span>
        {aba === 'ativas' && <span>Corte, Costura e Montagem somados a partir dos lançamentos de produção.</span>}
      </footer>
    </section>
  )
}

export default QuadroRemessas

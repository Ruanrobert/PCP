import Icon from './Icon'
import { Alert } from './ui'
import { useEscape } from './useEscape'
import { STATUS_REMESSA } from '../pcpUtils'

// Formulário de cadastro/edição de remessa. O estado e o envio continuam no App
// (mesmos campos e mesmo contrato de POST/PUT /api/remessas).
export function RemessaFormModal({ remessa, formulario, onCampo, onSubmit, onFechar, salvando, erro }) {
  const editando = Boolean(remessa)
  useEscape(true, () => {
    if (!salvando) onFechar()
  })

  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !salvando) onFechar()
      }}
    >
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="titulo-modal-remessa">
        <header className="modal-head">
          <span className="modal-icon">
            <Icon name={editando ? 'edit' : 'plus'} size={18} />
          </span>
          <div>
            <h2 id="titulo-modal-remessa">{editando ? `Editar remessa #${remessa.numero}` : 'Nova remessa'}</h2>
            <p>
              {editando
                ? 'Altere os dados da remessa. A alteração fica registrada no histórico.'
                : 'Cadastre uma remessa de produção para acompanhar no PCP.'}
            </p>
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={onFechar}
            disabled={salvando}
            aria-label="Fechar formulário"
          >
            <Icon name="close" size={18} />
          </button>
        </header>

        <form onSubmit={onSubmit} noValidate={false}>
          <div className="modal-body">
            <fieldset className="form-section">
              <legend>Identificação</legend>
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">
                    Número da remessa <em>*</em>
                  </span>
                  <input
                    id="remessa-numero"
                    className="input input-mono"
                    name="numero"
                    value={formulario.numero}
                    onChange={onCampo}
                    maxLength={50}
                    required
                    placeholder="Ex.: 605"
                    autoFocus
                  />
                </label>
                <label className="field field-span-2">
                  <span className="field-label">
                    Descrição / cliente <em>*</em>
                  </span>
                  <input
                    id="remessa-descricao"
                    className="input"
                    name="descricao"
                    value={formulario.descricao}
                    onChange={onCampo}
                    maxLength={200}
                    required
                    placeholder="Nome do cliente ou descrição do pedido"
                  />
                </label>
              </div>
            </fieldset>

            <fieldset className="form-section">
              <legend>Planejamento</legend>
              <div className="form-grid form-grid-3">
                <label className="field">
                  <span className="field-label">
                    Quantidade de pares <em>*</em>
                  </span>
                  <input
                    id="remessa-quantidade"
                    className="input num"
                    name="quantidade_pares"
                    type="number"
                    min="0"
                    step="1"
                    value={formulario.quantidade_pares}
                    onChange={onCampo}
                    required
                  />
                </label>
                <label className="field">
                  <span className="field-label">Data de entrega</span>
                  <input
                    id="remessa-entrega"
                    className="input"
                    name="data_entrega"
                    type="date"
                    value={formulario.data_entrega}
                    onChange={onCampo}
                  />
                </label>
                <label className="field">
                  <span className="field-label">
                    Status <em>*</em>
                  </span>
                  <select
                    id="remessa-status"
                    className="input"
                    name="status"
                    value={formulario.status}
                    onChange={onCampo}
                    required
                  >
                    {STATUS_REMESSA.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </fieldset>

            {erro && <Alert tipo="error">{erro}</Alert>}
          </div>

          <footer className="modal-foot">
            <span className="hint">
              Campos com <em>*</em> são obrigatórios.
            </span>
            <div className="modal-foot-actions">
              <button type="button" className="btn btn-secondary" onClick={onFechar} disabled={salvando}>
                Cancelar
              </button>
              <button id="remessa-salvar" type="submit" className="btn btn-primary" disabled={salvando}>
                {salvando ? <span className="spinner" /> : <Icon name="check" size={16} />}
                {salvando ? 'Salvando...' : 'Salvar remessa'}
              </button>
            </div>
          </footer>
        </form>
      </section>
    </div>
  )
}

export function ConfirmDialog({ titulo, children, rotuloConfirmar, perigo, ocupado, onConfirmar, onCancelar }) {
  useEscape(true, () => {
    if (!ocupado) onCancelar()
  })
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !ocupado) onCancelar()
      }}
    >
      <section className="modal modal-sm" role="alertdialog" aria-modal="true" aria-labelledby="titulo-confirmacao">
        <div className="confirm-body">
          <span className={`confirm-icon ${perigo ? 'is-danger' : ''}`}>
            <Icon name={perigo ? 'trash' : 'info'} size={20} />
          </span>
          <div>
            <h2 id="titulo-confirmacao">{titulo}</h2>
            <div className="confirm-text">{children}</div>
          </div>
        </div>
        <footer className="modal-foot">
          <span />
          <div className="modal-foot-actions">
            <button type="button" className="btn btn-secondary" onClick={onCancelar} disabled={ocupado} autoFocus>
              Cancelar
            </button>
            <button
              id="confirmar-acao"
              type="button"
              className={`btn ${perigo ? 'btn-danger' : 'btn-primary'}`}
              onClick={onConfirmar}
              disabled={ocupado}
            >
              {ocupado && <span className="spinner" />}
              {rotuloConfirmar}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

export function Toasts({ itens, onFechar }) {
  return (
    <div className="toast-stack" aria-live="polite">
      {itens.map((t) => (
        <div key={t.id} className={`toast toast-${t.tipo}`} role={t.tipo === 'error' ? 'alert' : 'status'}>
          <Icon name={t.tipo === 'error' ? 'alert' : 'check'} size={18} />
          <span>{t.texto}</span>
          <button type="button" className="toast-close" onClick={() => onFechar(t.id)} aria-label="Fechar aviso">
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

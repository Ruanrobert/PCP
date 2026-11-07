// Componentes visuais reutilizáveis do design system.
import Icon from './Icon'
import { formatarNumero, varianteStatus } from '../pcpUtils'

const iconeEtapa = { Corte: 'scissors', Costura: 'stitch', Montagem: 'box' }
const classeEtapa = { Corte: 'corte', Costura: 'costura', Montagem: 'montagem' }

export function StatusBadge({ status }) {
  return <span className={`badge badge-${varianteStatus(status)}`}>{status}</span>
}

export function Badge({ variante = 'neutral', children }) {
  return <span className={`badge badge-${variante}`}>{children}</span>
}

export function StageTag({ etapa }) {
  return (
    <span className={`stage-tag stage-${classeEtapa[etapa] || 'corte'}`}>
      <Icon name={iconeEtapa[etapa] || 'layers'} size={13} strokeWidth={2} />
      {etapa}
    </span>
  )
}

export function StageIcon({ etapa, size = 18 }) {
  return (
    <span className={`stage-icon stage-${classeEtapa[etapa] || 'corte'}`}>
      <Icon name={iconeEtapa[etapa] || 'layers'} size={size} />
    </span>
  )
}

export function Kpi({ icone, tom = 'primary', rotulo, valor, rodape, carregando, destaque }) {
  return (
    <article className={`kpi ${destaque ? `kpi-${destaque}` : ''}`}>
      <div className="kpi-top">
        <span className="kpi-label">{rotulo}</span>
        <span className={`kpi-icon tone-${tom}`}>
          <Icon name={icone} size={18} />
        </span>
      </div>
      <strong className="kpi-value">{carregando ? <span className="skeleton skeleton-kpi" /> : valor}</strong>
      {rodape && <span className="kpi-foot">{rodape}</span>}
    </article>
  )
}

export function ProgressBar({ valor, tom = 'primary', rotulo }) {
  const pct = Math.max(0, Math.min(100, Number(valor || 0)))
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={rotulo}
    >
      <div className={`progress-fill tone-fill-${tom}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

// Quantidade por etapa com percentual do planejado (ou travessão quando zero).
export function StageValue({ valor, total, etapa }) {
  const qtd = Number(valor || 0)
  if (!qtd) return <span className="text-faint">—</span>
  const pct = Number(total) > 0 ? Math.round((qtd / Number(total)) * 100) : 0
  return (
    <div className="stage-value">
      <span className="num">{formatarNumero(qtd)}</span>
      <ProgressBar valor={pct} tom={classeEtapa[etapa]} rotulo={`${etapa}: ${pct}% do planejado`} />
      <small>{pct}%</small>
    </div>
  )
}

export function Alert({ tipo = 'error', titulo, children, acao }) {
  const icone = tipo === 'error' ? 'alert' : tipo === 'success' ? 'check' : 'info'
  return (
    <div className={`alert alert-${tipo}`} role={tipo === 'error' ? 'alert' : 'status'}>
      <Icon name={icone} size={18} />
      <div className="alert-body">
        {titulo && <strong>{titulo}</strong>}
        <span>{children}</span>
      </div>
      {acao}
    </div>
  )
}

export function EmptyState({ icone = 'inbox', titulo, children, acao }) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icone} size={22} />
      </span>
      <strong>{titulo}</strong>
      {children && <p>{children}</p>}
      {acao}
    </div>
  )
}

// Linhas "esqueleto" exibidas na tabela durante o carregamento.
export function SkeletonRows({ colunas, linhas = 3 }) {
  return Array.from({ length: linhas }, (_, i) => (
    <tr key={`sk-${i}`} className="skeleton-row">
      {Array.from({ length: colunas }, (__, j) => (
        <td key={j}>
          <span className="skeleton" />
        </td>
      ))}
    </tr>
  ))
}

export function Card({ titulo, subtitulo, icone, acoes, children, className = '', semPadding }) {
  return (
    <section className={`card ${className}`}>
      {(titulo || acoes) && (
        <header className="card-header">
          <div className="card-heading">
            {icone && (
              <span className="card-icon">
                <Icon name={icone} size={17} />
              </span>
            )}
            <div>
              {titulo && <h2 className="card-title">{titulo}</h2>}
              {subtitulo && <p className="card-sub">{subtitulo}</p>}
            </div>
          </div>
          {acoes && <div className="card-actions">{acoes}</div>}
        </header>
      )}
      <div className={semPadding ? '' : 'card-body'}>{children}</div>
    </section>
  )
}

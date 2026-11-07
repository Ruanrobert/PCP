// Utilitários compartilhados entre as telas do PCP Industrial.

export const API = 'http://127.0.0.1:8000/api'

export const ETAPAS = ['Corte', 'Costura', 'Montagem']

// Status aceitos no formulário de remessa (mesmos valores já usados no sistema).
export const STATUS_REMESSA = ['A programar', 'Em andamento', 'Concluída', 'Pausada']

// Valor gravado pelo backend na exclusão lógica (DELETE /api/remessas/{id}).
export const STATUS_EXCLUIDA = 'Excluída'

export function formatarNumero(valor) {
  return Number(valor || 0).toLocaleString('pt-BR')
}

export function formatarData(valor) {
  if (!valor) return 'Sem data'
  const partes = valor.split('-')
  if (partes.length !== 3) return valor
  return `${partes[2]}/${partes[1]}/${partes[0]}`
}

export function formatarDataHora(valor) {
  if (!valor) return 'Data não informada'
  const data = new Date(valor)
  if (Number.isNaN(data.getTime())) return valor
  return data.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

// "novembro de 2026" a partir de AAAA-MM-DD.
export function formatarMesAno(valor) {
  if (!valor) return 'Sem data de entrega'
  const [ano, mes] = valor.split('-').map(Number)
  if (!ano || !mes) return valor
  return new Date(Date.UTC(ano, mes - 1, 15)).toLocaleDateString('pt-BR', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

// Data local de hoje no formato AAAA-MM-DD (mesmo formato salvo no banco).
export function hojeISO() {
  const d = new Date()
  const mes = String(d.getMonth() + 1).padStart(2, '0')
  const dia = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mes}-${dia}`
}

// Falhas de rede no fetch chegam como TypeError ("Failed to fetch").
export function mensagemErro(erro, padrao) {
  if (erro instanceof TypeError) {
    return 'Não foi possível conectar à API. Verifique se o backend está rodando na porta 8000.'
  }
  return erro?.message || padrao
}

// Chamada à API com tratamento uniforme de erro (usa `detail` do FastAPI quando houver).
export async function requisitar(caminho, opcoes = {}, padrao = 'Erro ao comunicar com a API.') {
  const resposta = await fetch(`${API}${caminho}`, opcoes)
  let dados
  try {
    dados = await resposta.json()
  } catch {
    dados = null
  }
  if (!resposta.ok) {
    const detalhe = dados?.detail
    let mensagem = padrao
    if (typeof detalhe === 'string') mensagem = detalhe
    else if (Array.isArray(detalhe) && detalhe[0]?.msg) mensagem = `Dados inválidos: ${detalhe[0].msg}`
    else if (resposta.status === 404) mensagem = 'Registro não encontrado.'
    const erro = new Error(mensagem)
    erro.status = resposta.status
    throw erro
  }
  return dados
}

export function percentual(valor, total) {
  const t = Number(total || 0)
  if (t <= 0) return 0
  return Math.round((Number(valor || 0) / t) * 100)
}

export function varianteStatus(status) {
  switch (status) {
    case 'A programar':
      return 'pending'
    case 'Em andamento':
      return 'progress'
    case 'Concluída':
      return 'done'
    case 'Pausada':
      return 'paused'
    case STATUS_EXCLUIDA:
      return 'deleted'
    default:
      return 'neutral'
  }
}

// Diferença em dias corridos entre a data de entrega e hoje (negativo = vencida).
export function diasAteEntrega(dataEntrega, hoje = hojeISO()) {
  if (!dataEntrega) return null
  const alvo = Date.parse(`${dataEntrega}T00:00:00Z`)
  const base = Date.parse(`${hoje}T00:00:00Z`)
  if (Number.isNaN(alvo) || Number.isNaN(base)) return null
  return Math.round((alvo - base) / 86400000)
}

export function descreverPrazo(dias, status) {
  if (dias === null) return { texto: 'Sem data', variante: 'neutral' }
  if (status === 'Concluída') return { texto: 'Concluída', variante: 'done' }
  if (dias < 0) return { texto: `Vencida há ${-dias} dia(s)`, variante: 'deleted' }
  if (dias === 0) return { texto: 'Entrega hoje', variante: 'pending' }
  if (dias <= 7) return { texto: `Em ${dias} dia(s)`, variante: 'pending' }
  return { texto: `Em ${dias} dia(s)`, variante: 'done' }
}

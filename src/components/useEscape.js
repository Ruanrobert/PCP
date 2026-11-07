import { useEffect } from 'react'

// Fecha modais/painéis com a tecla Esc enquanto estiverem abertos.
export function useEscape(ativo, aoPressionar) {
  useEffect(() => {
    if (!ativo) return undefined
    function tratar(evento) {
      if (evento.key === 'Escape') aoPressionar()
    }
    window.addEventListener('keydown', tratar)
    return () => window.removeEventListener('keydown', tratar)
  }, [ativo, aoPressionar])
}

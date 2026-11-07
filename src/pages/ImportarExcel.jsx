import Icon from '../components/Icon'

// A importação de Excel ainda não existe no sistema; a tela informa isso com clareza.
function ImportarExcel({ onNavegar, onNovaRemessa }) {
  return (
    <section className="card">
      <div className="feature-empty">
        <span className="feature-empty-icon">
          <Icon name="upload" size={28} />
        </span>
        <span className="badge badge-pending">Em desenvolvimento</span>
        <h2>Importação de planilhas ainda não disponível</h2>
        <p>
          Esta funcionalidade ainda não foi implementada no PCP Industrial. Nenhum arquivo é enviado ou processado
          nesta tela. Para incluir remessas, utilize o cadastro manual.
        </p>
        <div className="feature-empty-actions">
          <button id="importar-nova-remessa" type="button" className="btn btn-primary" onClick={onNovaRemessa}>
            <Icon name="plus" size={16} />
            Nova remessa
          </button>
          <button
            id="importar-ir-quadro"
            type="button"
            className="btn btn-secondary"
            onClick={() => onNavegar('Quadro de remessas')}
          >
            Ir para o Quadro de remessas
          </button>
        </div>
      </div>
    </section>
  )
}

export default ImportarExcel

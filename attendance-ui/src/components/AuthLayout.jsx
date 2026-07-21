// Split-panel auth shell: blue promo card (left) + content slot (right).
const PROMO_IMG =
  'https://images.unsplash.com/photo-1627556704290-2b1f5853ff78?q=80&w=2070&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D'

export default function AuthLayout({ children }) {
  return (
    <div className="auth-wrap">
      <div className="auth-promo">
        <img className="promo-img" src={PROMO_IMG} alt="" />
        <h2>University Management System</h2>
        <p>Prioritize user experience by creating an intuitive interface that is easy to
          navigate, ensuring quick and hassle-free access to the system</p>
        <div className="prepared">
          <div className="line" />
          Prepared by · Rean
        </div>
      </div>
      <div className="auth-panel">
        <div className="auth-card">{children}</div>
      </div>
    </div>
  )
}

// Mirrors mycamu-react @components/MatIcon
export default function MatIcon({ name, className = '', disabled, onClick, ...rest }) {
  return (
    <span
      data-icon-disabled={disabled}
      className={`material-symbols-rounded ${className}`}
      onClick={disabled ? null : onClick}
      {...rest}
    >
      {name}
    </span>
  )
}

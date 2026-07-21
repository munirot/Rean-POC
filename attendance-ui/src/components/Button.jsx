import BootstrapButton from 'react-bootstrap/Button'
import MatIcon from './MatIcon'

// Mirrors mycamu-react @components/Button: react-bootstrap Button + optional MatIcon.
const BUTTONICON = {
  edit: 'edit_square', new: 'add', add: 'add', cancel: 'close', config: 'settings',
  upload: 'upload', download: 'download', search: 'search', print: 'print', delete: 'delete',
  filter: 'filter_list', check: 'check', more: 'more_horiz', reset: 'restart_alt',
  save: 'save', photo: 'add_to_photos', qrScanner: 'qr_code_scanner', ArrowBack: 'arrow_back',
  camera: 'photo_camera', stop: 'stop_circle', flip: 'cameraswitch', remove: 'remove',
}

export default function Button({ icon, className = '', children, ...rest }) {
  return (
    <BootstrapButton className={className} {...rest}>
      {icon && <MatIcon name={BUTTONICON[icon] || icon} className="me-1" />}
      {children}
    </BootstrapButton>
  )
}

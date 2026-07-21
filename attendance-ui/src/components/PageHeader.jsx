import { Stack } from 'react-bootstrap'
import MatIcon from './MatIcon'

// Mirrors mycamu-react @components/PageHeader
export default function PageHeader({ heading, subHeading, back, children }) {
  return (
    <Stack direction="horizontal" className="pb-4 header_back page-header_block flex-wrap" gap={3}>
      {back && <MatIcon name="west" onClick={back} />}
      <div>
        {heading && <p className="m-0 fs-5 text-primary fw-bold">{heading}</p>}
        {subHeading && <p className="m-0 fs-3 text-secondary">{subHeading}</p>}
      </div>
      <div className="no-right ms-auto">
        <Stack direction="horizontal" gap={2} className="flex-wrap">
          {children}
        </Stack>
      </div>
    </Stack>
  )
}

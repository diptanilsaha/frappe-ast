import { useFrappeGetCall, useFrappeGetDocList, useFrappePostCall } from 'frappe-react-sdk'

export const Importer = ({ data }) => {
	const { call } = useFrappePostCall('run_doc_method')
	const { data: count } = useFrappeGetCall('demo.api.get_count')
	const { data: tasks } = useFrappeGetDocList('Demo Task', { fields: ['name'], orderBy: { field: 'status', order: 'asc' } })
	const onImport = () => call({ docs: data.doc, method: 'finish' })
	return null
}

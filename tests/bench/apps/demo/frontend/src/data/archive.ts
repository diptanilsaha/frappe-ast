import { createListResource, createResource } from 'frappe-ui'
import { taskList } from '@/data/taskList'
const notes = createListResource({ doctype: 'Demo Task' })

export function archiveAll() {
	taskList.runDocMethod.submit({ name: 'T-1', method: 'archive' })
	notes.runDocMethod.submit({ name: 'T-1', method: 'finish' })
	createResource({ url: 'run_doc_method', makeParams: () => ({ dt: 'Demo Task', dn: 'T-1', method: 'finish' }) })
}

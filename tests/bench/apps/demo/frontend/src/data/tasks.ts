import { call, createResource, createListResource, useDoctype, useDoc } from 'frappe-ui'
import { API } from './api'
export const COUNT_URL = 'demo.api.get_count'
const tasks = useDoctype('Demo Task')

export function finishAll() {
	tasks.runDocMethod.submit({ name: 'T-1', method: 'finish' })
	tasks.runDocMethod.submit({ name: 'T-1', method: 'private_helper' })
	tasks.runMethod.submit({ method: 'close_task' })
	useDoc({ doctype: 'Demo Task', name: 'T-1', methods: { done: 'finish', gone: { name: 'vanished' } } })
	createResource({ url: COUNT_URL })
	call(API.open, {})
	call('frappe.client.set_value', { doctype: 'Demo Task', name: 'T-1', fieldname: { status: 'Closed', priority: 'High' } })
	createListResource({
		doctype: 'Demo Task',
		fields: ['name', 'status', 'statsu', 'items.note', 'count(name) as n'],
		filters: { owner: 'x', color: 'red' },
	})
}

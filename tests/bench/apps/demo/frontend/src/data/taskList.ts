import { createListResource } from 'frappe-ui'
export const taskList = createListResource({ doctype: 'Demo Task', fields: ['name'] })

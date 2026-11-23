<template>
  <div>
    <TaskList :tasks="tasks.data" @select="openTask" />
    <Button @click="save()">Save</Button>
    <NumberChart api-url="demo.api.get_count" :fallback="'demo.api.open_tasks'" title="demo.api.x" />
  </div>
</template>

<script setup lang="ts">
import { Button, createResource, createListResource, useCall } from 'frappe-ui'
import TaskList from './TaskList.vue'

const tasks = createListResource({ doctype: 'Demo Task', fields: ['name'] })
const counts = createResource({ url: 'demo.api.get_count' })
const posted = useCall({ url: '/api/v2/method/demo.api.open_tasks', method: 'POST' })

function openTask(name: string) {}
function save() {}
</script>

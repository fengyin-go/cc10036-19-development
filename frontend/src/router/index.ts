import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import LayoutImportView from '@/views/LayoutImportView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径放在 /:moduleKey 前面，避免被动态模块路径吃掉
    { path: '/layout-import', name: 'layout-import', component: LayoutImportView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router

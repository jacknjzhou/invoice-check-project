import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

export interface Invoice {
  id: number
  file_name: string
  file_hash: string
  file_type: string
  status: string
  invoice_type?: string
  invoice_code?: string
  invoice_number?: string
  issue_date?: string
  amount_ex_tax?: number
  tax_amount?: number
  amount_total?: number
  seller_name?: string
  buyer_name?: string
  seller_tax_id?: string
  buyer_tax_id?: string
  items?: Array<{ name: string; amount: number }>
  remark?: string
  duplicate_of_id?: number
  error_msg?: string
  created_at: string
}

export interface InvoicePage {
  total: number
  items: Invoice[]
}

export interface ReportSummary {
  total_amount: number
  total_count: number
  by_month: Array<{ month: string; amount: number; count: number }>
  by_type: Array<{ type: string; amount: number }>
  by_seller: Array<{ seller: string; amount: number }>
}

export interface Setting {
  provider: string
  base_url: string
  api_key: string
  extract_model: string
  chat_model: string
}

export interface ChatResponse {
  answer: string
  context_note?: string
}

// 发票
export const uploadInvoices = (files: File[]) => {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  return api.post<Invoice[]>('/invoices', form)
}

export const listInvoices = (params?: {
  status?: string
  invoice_type?: string
  month?: string
  seller?: string
  page?: number
  page_size?: number
}) => api.get<InvoicePage>('/invoices', { params })

export const getInvoice = (id: number) => api.get<Invoice>(`/invoices/${id}`)

export const updateInvoice = (id: number, data: Partial<Invoice>) =>
  api.patch<Invoice>(`/invoices/${id}`, data)

export const deleteInvoice = (id: number) => api.delete(`/invoices/${id}`)

export const reprocessInvoice = (id: number) => api.post<Invoice>(`/invoices/${id}/reprocess`)

// 报表
export const getReportSummary = (params?: { month?: string; invoice_type?: string }) =>
  api.get<ReportSummary>('/reports/summary', { params })

export const exportReport = (params?: { month?: string; invoice_type?: string }) =>
  api.get('/reports/export', { params, responseType: 'blob' })

// 对话
export const chat = (question: string, messages: Array<{ role: string; content: string }> = []) =>
  api.post<ChatResponse>('/chat', { question, messages })

// 设置
export const getSettings = () => api.get<Setting>('/settings')

export const updateSettings = (data: Setting) => api.put<Setting>('/settings', data)

export const testConnection = () => api.post<{ ok: boolean; message: string }>('/settings/test')

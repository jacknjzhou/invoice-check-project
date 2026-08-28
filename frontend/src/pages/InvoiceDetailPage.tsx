import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Button, Form, Input, InputNumber, message, Spin, Tag, Space } from 'antd'
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons'
import { getInvoice, updateInvoice, type Invoice } from '../api'

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    const fetch = async () => {
      try {
        const res = await getInvoice(Number(id))
        setInvoice(res.data)
        form.setFieldsValue({
          invoice_type: res.data.invoice_type,
          invoice_code: res.data.invoice_code,
          invoice_number: res.data.invoice_number,
          issue_date: res.data.issue_date,
          amount_ex_tax: res.data.amount_ex_tax,
          tax_amount: res.data.tax_amount,
          amount_total: res.data.amount_total,
          seller_name: res.data.seller_name,
          buyer_name: res.data.buyer_name,
          seller_tax_id: res.data.seller_tax_id,
          buyer_tax_id: res.data.buyer_tax_id,
          remark: res.data.remark,
        })
      } catch (err: any) {
        message.error('加载失败')
      } finally {
        setLoading(false)
      }
    }
    fetch()
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    try {
      const values = form.getFieldsValue()
      await updateInvoice(Number(id), values)
      message.success('保存成功')
    } catch (err: any) {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
  if (!invoice) return <div>发票不存在</div>

  const statusColors: Record<string, string> = {
    pending: 'default',
    processing: 'processing',
    done: 'success',
    error: 'error',
    duplicate: 'warning',
  }
  const statusLabels: Record<string, string> = {
    pending: '待处理',
    processing: '处理中',
    done: '已完成',
    error: '失败',
    duplicate: '重复',
  }

  return (
    <Card
      title={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/invoices')}>
            返回
          </Button>
          <span>发票详情</span>
          <Tag color={statusColors[invoice.status]}>{statusLabels[invoice.status]}</Tag>
        </Space>
      }
      extra={
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
          保存修改
        </Button>
      }
    >
      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ flex: 1 }}>
          <h3>原件预览</h3>
          {invoice.file_type === 'image' ? (
            <img
              src={`/api/invoices/${invoice.id}/file`}
              alt={invoice.file_name}
              style={{ maxWidth: '100%', border: '1px solid #ddd' }}
            />
          ) : (
            <iframe
              src={`/api/invoices/${invoice.id}/file`}
              style={{ width: '100%', height: '600px', border: '1px solid #ddd' }}
              title="PDF 预览"
            />
          )}
        </div>
        <div style={{ flex: 1 }}>
          <h3>提取字段（可修改）</h3>
          <Form form={form} layout="vertical">
            <Form.Item name="invoice_type" label="发票类型">
              <Input />
            </Form.Item>
            <Form.Item name="invoice_code" label="发票代码">
              <Input />
            </Form.Item>
            <Form.Item name="invoice_number" label="发票号码">
              <Input />
            </Form.Item>
            <Form.Item name="issue_date" label="开票日期">
              <Input placeholder="YYYY-MM-DD" />
            </Form.Item>
            <Form.Item name="amount_ex_tax" label="不含税金额">
              <InputNumber style={{ width: '100%' }} precision={2} />
            </Form.Item>
            <Form.Item name="tax_amount" label="税额">
              <InputNumber style={{ width: '100%' }} precision={2} />
            </Form.Item>
            <Form.Item name="amount_total" label="价税合计">
              <InputNumber style={{ width: '100%' }} precision={2} />
            </Form.Item>
            <Form.Item name="seller_name" label="销售方">
              <Input />
            </Form.Item>
            <Form.Item name="seller_tax_id" label="销售方纳税人识别号">
              <Input />
            </Form.Item>
            <Form.Item name="buyer_name" label="购买方">
              <Input />
            </Form.Item>
            <Form.Item name="buyer_tax_id" label="购买方纳税人识别号">
              <Input />
            </Form.Item>
            <Form.Item name="remark" label="备注">
              <Input.TextArea rows={3} />
            </Form.Item>
          </Form>
        </div>
      </div>
      {invoice.error_msg && (
        <div style={{ marginTop: 16, padding: 12, background: '#fff2f0', border: '1px solid #ffccc7' }}>
          <strong>错误信息：</strong> {invoice.error_msg}
        </div>
      )}
    </Card>
  )
}



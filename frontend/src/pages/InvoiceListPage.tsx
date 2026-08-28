import { useEffect, useState } from 'react'
import { Table, Card, Tag, Button, Space, Input, Select, message, Popconfirm } from 'antd'
import { ReloadOutlined, DeleteOutlined, EyeOutlined, RedoOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { listInvoices, deleteInvoice, reprocessInvoice, type Invoice } from '../api'

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

export default function InvoiceListPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<Invoice[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState<string>()
  const [sellerFilter, setSellerFilter] = useState('')

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await listInvoices({
        status: statusFilter,
        seller: sellerFilter || undefined,
        page,
        page_size: pageSize,
      })
      setData(res.data.items)
      setTotal(res.data.total)
    } catch (err: any) {
      message.error('加载失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    // 轮询刷新（处理中的发票）
    const timer = setInterval(fetchData, 5000)
    return () => clearInterval(timer)
  }, [page, pageSize, statusFilter])

  const handleDelete = async (id: number) => {
    try {
      await deleteInvoice(id)
      message.success('删除成功')
      fetchData()
    } catch (err: any) {
      message.error('删除失败')
    }
  }

  const handleReprocess = async (id: number) => {
    try {
      await reprocessInvoice(id)
      message.success('已加入重新识别队列')
      fetchData()
    } catch (err: any) {
      message.error('操作失败')
    }
  }

  const columns = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColors[s]}>{statusLabels[s] || s}</Tag>,
    },
    {
      title: '文件名',
      dataIndex: 'file_name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'invoice_type',
      width: 150,
      render: (v: string) => v || '-',
    },
    {
      title: '开票日期',
      dataIndex: 'issue_date',
      width: 120,
      render: (v: string) => v || '-',
    },
    {
      title: '金额',
      dataIndex: 'amount_total',
      width: 100,
      align: 'right' as const,
      render: (v: number) => (v != null ? `¥${v.toFixed(2)}` : '-'),
    },
    {
      title: '商户',
      dataIndex: 'seller_name',
      ellipsis: true,
      render: (v: string) => v || '-',
    },
    {
      title: '操作',
      width: 240,
      fixed: 'right' as const,
      render: (_: any, record: Invoice) => (
        <Space size="small" wrap>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => navigate(`/invoices/${record.id}`)}>
            详情
          </Button>
          <Button type="link" size="small" icon={<RedoOutlined />} onClick={() => handleReprocess(record.id)}>
            重识别
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="发票列表"
      extra={
        <Space>
          <Select
            placeholder="状态筛选"
            allowClear
            style={{ width: 120 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'pending', label: '待处理' },
              { value: 'processing', label: '处理中' },
              { value: 'done', label: '已完成' },
              { value: 'error', label: '失败' },
              { value: 'duplicate', label: '重复' },
            ]}
          />
          <Input
            placeholder="商户名称"
            allowClear
            style={{ width: 150 }}
            value={sellerFilter}
            onChange={e => setSellerFilter(e.target.value)}
            onPressEnter={fetchData}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={loading}
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: t => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
      />
    </Card>
  )
}

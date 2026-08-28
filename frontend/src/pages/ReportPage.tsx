import { useEffect, useState } from 'react'
import { Card, Statistic, Table, Button, Space, Select, message } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { getReportSummary, exportReport, type ReportSummary } from '../api'

export default function ReportPage() {
  const [data, setData] = useState<ReportSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [monthFilter, setMonthFilter] = useState<string>()
  const [typeFilter, setTypeFilter] = useState<string>()

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await getReportSummary({ month: monthFilter, invoice_type: typeFilter })
      setData(res.data)
    } catch (err: any) {
      message.error('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [monthFilter, typeFilter])

  const handleExport = async () => {
    try {
      const res = await exportReport({ month: monthFilter, invoice_type: typeFilter })
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', '发票报表.xlsx')
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch (err: any) {
      message.error('导出失败')
    }
  }

  const monthColumns = [
    { title: '月份', dataIndex: 'month', key: 'month' },
    { title: '金额', dataIndex: 'amount', key: 'amount', render: (v: number) => `¥${v.toFixed(2)}` },
    { title: '笔数', dataIndex: 'count', key: 'count' },
  ]

  const typeColumns = [
    { title: '类型', dataIndex: 'type', key: 'type' },
    { title: '金额', dataIndex: 'amount', key: 'amount', render: (v: number) => `¥${v.toFixed(2)}` },
  ]

  const sellerColumns = [
    { title: '商户', dataIndex: 'seller', key: 'seller' },
    { title: '金额', dataIndex: 'amount', key: 'amount', render: (v: number) => `¥${v.toFixed(2)}` },
  ]

  return (
    <Card
      title="报表统计"
      extra={
        <Space>
          <Select
            placeholder="月份筛选"
            allowClear
            style={{ width: 120 }}
            value={monthFilter}
            onChange={setMonthFilter}
            options={
              data?.by_month.map(m => ({ value: m.month, label: m.month })) || []
            }
          />
          <Select
            placeholder="类型筛选"
            allowClear
            style={{ width: 150 }}
            value={typeFilter}
            onChange={setTypeFilter}
            options={
              data?.by_type.map(t => ({ value: t.type, label: t.type })) || []
            }
          />
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleExport}>
            导出 Excel
          </Button>
        </Space>
      }
    >
      {data && (
        <>
          <div style={{ display: 'flex', gap: 24, marginBottom: 24 }}>
            <Card style={{ flex: 1 }}>
              <Statistic title="总金额" value={data.total_amount} precision={2} prefix="¥" />
            </Card>
            <Card style={{ flex: 1 }}>
              <Statistic title="总笔数" value={data.total_count} suffix="笔" />
            </Card>
          </div>

          <div style={{ display: 'flex', gap: 24 }}>
            <Card title="按月汇总" style={{ flex: 1 }}>
              <Table
                rowKey="month"
                columns={monthColumns}
                dataSource={data.by_month}
                pagination={false}
                size="small"
              />
            </Card>
            <Card title="按类型汇总" style={{ flex: 1 }}>
              <Table
                rowKey="type"
                columns={typeColumns}
                dataSource={data.by_type}
                pagination={false}
                size="small"
              />
            </Card>
            <Card title="商户 TOP 20" style={{ flex: 1 }}>
              <Table
                rowKey="seller"
                columns={sellerColumns}
                dataSource={data.by_seller}
                pagination={false}
                size="small"
              />
            </Card>
          </div>
        </>
      )}
    </Card>
  )
}

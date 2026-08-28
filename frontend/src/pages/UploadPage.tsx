import { useState } from 'react'
import { Upload, message, Card, Progress, Button } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { uploadInvoices } from '../api'

const { Dragger } = Upload

export default function UploadPage() {
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)

  const handleUpload = async (files: File[]) => {
    if (files.length === 0) return
    setUploading(true)
    setProgress(0)
    try {
      const res = await uploadInvoices(files)
      setProgress(100)
      message.success(`成功上传 ${res.data.length} 张发票，正在后台识别...`)
    } catch (err: any) {
      message.error('上传失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <Card title="上传发票">
      <Dragger
        multiple
        accept="image/*,.pdf"
        showUploadList={false}
        beforeUpload={(file, fileList) => {
          // 阻止自动上传，收集所有文件
          if (file === fileList[fileList.length - 1]) {
            handleUpload(fileList as unknown as File[])
          }
          return false
        }}
        disabled={uploading}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽发票文件到此区域上传</p>
        <p className="ant-upload-hint">支持图片（JPG/PNG/WebP）和 PDF 格式，可批量上传</p>
      </Dragger>
      {uploading && (
        <div style={{ marginTop: 16 }}>
          <Progress percent={progress} status="active" />
          <p style={{ textAlign: 'center', color: '#888' }}>正在上传...</p>
        </div>
      )}
      {!uploading && progress === 100 && (
        <div style={{ marginTop: 16, textAlign: 'center' }}>
          <Button type="link" href="/invoices">
            查看发票列表
          </Button>
        </div>
      )}
    </Card>
  )
}

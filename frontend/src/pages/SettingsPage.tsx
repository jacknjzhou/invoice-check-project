import { useEffect, useState } from 'react'
import { Card, Form, Input, Select, Button, message, Space } from 'antd'
import { SaveOutlined, ApiOutlined } from '@ant-design/icons'
import { getSettings, updateSettings, testConnection, type Setting } from '../api'

export default function SettingsPage() {
  const [form] = Form.useForm<Setting>()
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    const fetch = async () => {
      try {
        const res = await getSettings()
        form.setFieldsValue(res.data)
      } catch (err: any) {
        message.error('加载配置失败')
      }
    }
    fetch()
  }, [])

  const handleSave = async () => {
    setLoading(true)
    try {
      const values = form.getFieldsValue()
      await updateSettings(values)
      message.success('保存成功')
    } catch (err: any) {
      message.error('保存失败')
    } finally {
      setLoading(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await testConnection()
      if (res.data.ok) {
        message.success('连接成功')
      } else {
        message.error('连接失败: ' + res.data.message)
      }
    } catch (err: any) {
      message.error('测试失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setTesting(false)
    }
  }

  return (
    <Card title="模型设置">
      <Form form={form} layout="vertical" style={{ maxWidth: 600 }}>
        <Form.Item name="provider" label="提供商">
          <Select
            options={[
              { value: 'ollama', label: 'Ollama (本地)' },
              { value: 'openai', label: 'OpenAI' },
              { value: 'dashscope', label: '阿里云 DashScope' },
              { value: 'custom', label: '自定义 OpenAI 兼容' },
            ]}
          />
        </Form.Item>
        <Form.Item name="base_url" label="API Base URL">
          <Input placeholder="http://localhost:11434/v1" />
        </Form.Item>
        <Form.Item name="api_key" label="API Key">
          <Input.Password placeholder="可选，Ollama 本地无需填写" />
        </Form.Item>
        <Form.Item name="extract_model" label="提取模型">
          <Input placeholder="qwen2.5vl:7b" />
        </Form.Item>
        <Form.Item name="chat_model" label="对话模型">
          <Input placeholder="qwen2.5vl:7b" />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" icon={<SaveOutlined />} loading={loading} onClick={handleSave}>
              保存
            </Button>
            <Button icon={<ApiOutlined />} loading={testing} onClick={handleTest}>
              测试连接
            </Button>
          </Space>
        </Form.Item>
      </Form>
      <div style={{ marginTop: 24, padding: 16, background: '#f6f8fa', borderRadius: 6 }}>
        <h4>使用说明</h4>
        <ul style={{ paddingLeft: 20, margin: 0 }}>
          <li>Ollama 本地：确保已安装 Ollama 并拉取视觉模型，如 <code>ollama pull qwen2.5vl:7b</code></li>
          <li>OpenAI：填写 API Key，模型可选 gpt-4o 等支持视觉的模型</li>
          <li>DashScope：填写 API Key，模型可选 qwen-vl-max 等</li>
          <li>自定义：填写任意 OpenAI 兼容 API 的地址和模型名</li>
        </ul>
      </div>
    </Card>
  )
}

import { useState } from 'react'
import { Card, Input, Button, List, Typography, Spin, message } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import { chat } from '../api'

const { TextArea } = Input
const { Text } = Typography

interface Message {
  role: 'user' | 'assistant'
  content: string
  context_note?: string
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSend = async () => {
    if (!input.trim()) return
    const userMsg: Message = { role: 'user', content: input }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const history = messages.map(m => ({ role: m.role, content: m.content }))
      const res = await chat(input, history)
      const assistantMsg: Message = {
        role: 'assistant',
        content: res.data.answer,
        context_note: res.data.context_note,
      }
      setMessages(prev => [...prev, assistantMsg])
    } catch (err: any) {
      message.error('请求失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card title="智能问答" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, overflowY: 'auto', marginBottom: 16, minHeight: 400 }}>
        <List
          dataSource={messages}
          renderItem={item => (
            <List.Item style={{ display: 'block', textAlign: item.role === 'user' ? 'right' : 'left' }}>
              <div
                style={{
                  display: 'inline-block',
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: item.role === 'user' ? '#1890ff' : '#f0f0f0',
                  color: item.role === 'user' ? '#fff' : '#000',
                  maxWidth: '80%',
                  textAlign: 'left',
                }}
              >
                <div>{item.content}</div>
                {item.context_note && (
                  <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                    {item.context_note}
                  </Text>
                )}
              </div>
            </List.Item>
          )}
          locale={{ emptyText: '暂无对话，请输入问题' }}
        />
        {loading && (
          <div style={{ textAlign: 'center', padding: 16 }}>
            <Spin />
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <TextArea
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="例如：这个月报销总额多少？差旅费有多少？"
          autoSize={{ minRows: 2, maxRows: 4 }}
          onPressEnter={e => {
            if (!e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
        />
        <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading}>
          发送
        </Button>
      </div>
    </Card>
  )
}

import { useRef, useState, type DragEvent } from 'react'
import { Icon } from './Icon'

interface FileDropzoneProps {
  accept: string
  title: string
  hint: string
  file: File | null
  onFile: (file: File) => void
  disabled?: boolean
}
export function FileDropzone({ accept, title, hint, file, onFile, disabled }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    const dropped = event.dataTransfer.files[0]
    if (dropped) onFile(dropped)
  }

  return (
    <div
      className={`dropzone${dragging ? ' dragging' : ''}${disabled ? ' disabled' : ''}`}
      onDragEnter={(event) => { event.preventDefault(); setDragging(true) }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      role="button"
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(event) => event.key === 'Enter' && !disabled && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        onChange={(event) => event.target.files?.[0] && onFile(event.target.files[0])}
      />
      <div className="dropzone-icon"><Icon name={file ? 'check' : 'upload'} size={24} /></div>
      <strong>{file ? file.name : title}</strong>
      <span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · 点击重新选择` : hint}</span>
    </div>
  )
}

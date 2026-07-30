import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { FileText, UploadCloud, Check, Clock, AlertTriangle } from '../icons.jsx'

const POLL_MS = 1200

function StatusIcon({ status }) {
  if (status === 'complete') return <Check width={16} height={16} style={{ color: 'var(--green)' }} />
  if (status === 'failed') return <AlertTriangle width={16} height={16} style={{ color: 'var(--red)' }} />
  return <Clock width={16} height={16} style={{ color: 'var(--text-dim)' }} />
}

// Real drag-and-drop evidence upload. Uploads immediately on drop/select,
// then polls GET /documents until every file's extraction_status leaves
// "pending" -- the parent needs that settled state before it can usefully
// run the preliminary review (a still-extracting file has no cleaned_text yet).
export default function EvidenceDropzone({ caseId, onDocumentsChange }) {
  const { t } = useLanguage()
  const [documents, setDocuments] = useState([])
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => {
    onDocumentsChange && onDocumentsChange(documents)
  }, [documents]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => clearTimeout(pollRef.current)
  }, [])

  function schedulePoll() {
    clearTimeout(pollRef.current)
    pollRef.current = setTimeout(async () => {
      try {
        const docs = await api.listDocuments(caseId)
        setDocuments(docs)
        if (docs.some((d) => d.extraction_status === 'pending')) schedulePoll()
      } catch {
        // Transient network hiccup -- next user action (drop/continue) will retry the fetch.
      }
    }, POLL_MS)
  }

  async function handleFiles(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length) return
    setError('')
    setUploading(true)
    try {
      await api.uploadDocuments(caseId, files)
      const docs = await api.listDocuments(caseId)
      setDocuments(docs)
      schedulePoll()
    } catch (ex) {
      setError(ex.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div
        className={`dropzone${dragging ? ' dragging' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          handleFiles(e.dataTransfer.files)
        }}
        role="button"
        tabIndex={0}
      >
        <UploadCloud width={28} height={28} />
        <p className="dropzone-title">{uploading ? t('evidence.uploading') : t('evidence.dropTitle')}</p>
        <p className="sub">{t('evidence.dropHint')}</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="application/pdf,image/*"
          style={{ display: 'none' }}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
        />
      </div>

      {error && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginTop: 10 }}>{error}</p>}

      {documents.length > 0 && (
        <div className="ev-list">
          {documents.map((doc) => (
            <div className="ev-row" key={doc.id}>
              <FileText width={16} height={16} />
              {doc.original_filename}
              <span className="ev-kind">
                {doc.extraction_status === 'pending' ? t('evidence.statusPending') : doc.extraction_status === 'failed' ? t('evidence.statusFailed') : t('evidence.statusReady')}
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>
                <StatusIcon status={doc.extraction_status} />
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useRef, useState } from 'react';
import type { Drawing } from '../types';

const DISC_ORDER = ['建筑', '结构', '给排水', '电气', '未知'];

interface Props {
  drawings: Drawing[];
  selected: Drawing | null;
  onSelect: (d: Drawing) => void;
  onUpload: (f: File) => Promise<void>;
}

export default function Sidebar({ drawings, selected, onSelect, onUpload }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState('');

  const groups = DISC_ORDER
    .map((disc) => ({ disc, items: drawings.filter((d) => d.discipline === disc) }))
    .filter((g) => g.items.length > 0);

  const pick = async (f?: File) => {
    if (!f) return;
    setUploading(true);
    setErr('');
    try {
      await onUpload(f);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="sidebar">
      <div className="panel-head">
        <span>图纸库</span>
        <span className="count">{drawings.length}</span>
      </div>
      <div className="upload-zone">
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          hidden
          onChange={(e) => pick(e.target.files?.[0])}
        />
        <button
          className="btn-upload"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
        >
          {uploading ? '⟳ 解析中…' : '＋ 上传图纸压缩包'}
        </button>
        {err && <p className="upload-err">{err}</p>}
      </div>
      <div className="drawing-list">
        {groups.length === 0 && (
          <p className="empty-hint">上传 .zip 图纸包后，在此按专业选择图纸</p>
        )}
        {groups.map((g) => (
          <div key={g.disc} className="disc-group">
            <div className="disc-label">
              <span>{g.disc}</span>
              <span className="disc-count">{g.items.length}</span>
            </div>
            {g.items.map((d) => (
              <button
                key={d.id}
                className={`drawing-item ${selected?.id === d.id ? 'active' : ''}`}
                onClick={() => onSelect(d)}
                title={d.name}
              >
                {d.name}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

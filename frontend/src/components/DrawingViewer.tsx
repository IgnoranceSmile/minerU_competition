import { useEffect, useState } from 'react';
import { drawingPageUrl } from '../api';
import type { Drawing } from '../types';

interface Props {
  drawing: Drawing | null;
}

export default function DrawingViewer({ drawing }: Props) {
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [drawing?.id]);

  if (!drawing) {
    return (
      <main className="viewer viewer-empty">
        <div className="empty-card">
          <div className="empty-icon" />
          <p className="empty-title">未选择图纸</p>
          <p className="empty-desc">从左侧图纸库选择一张图纸开始查看</p>
        </div>
      </main>
    );
  }

  return (
    <main className="viewer">
      <div className="viewer-toolbar">
        <div className="vt-title" title={drawing.name}>{drawing.name}</div>
        <div className="vt-tools">
          {drawing.page_count > 1 && (
            <div className="page-nav">
              <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>‹</button>
              <span>{page + 1} / {drawing.page_count}</span>
              <button
                disabled={page >= drawing.page_count - 1}
                onClick={() => setPage((p) => p + 1)}
              >›</button>
            </div>
          )}
        </div>
      </div>
      <div className="viewer-stage">
        <div className="stage-inner">
          <img
            src={drawingPageUrl(drawing.id, page)}
            alt={drawing.name}
            draggable={false}
          />
        </div>
      </div>
    </main>
  );
}

/**
 * 离线音频队列(08 §1.1 OfflineCached;FR-02 弱网不丢已录音频)。
 * 网络失败时把待上传音频持久化在 IndexedDB,恢复连接后自动补传;
 * record_id 即幂等键:重复上传服务端按首次受理返回 duplicate(登记册 §2.2)。
 */

export interface PendingAudio {
  record_id: string;
  blob: Blob;
  duration_ms: number;
  queued_at: number;
  /** 上传头 X-Audio-Format(缺省 webm-opus);补传按原格式回传 */
  fmt?: string;
}

const DB_NAME = "vbadge";
const STORE = "pending_audio";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: "record_id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(db: IDBDatabase, mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = run(db.transaction(STORE, mode).objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/** 入队(同 record_id 覆盖,幂等) */
export async function enqueue(item: PendingAudio): Promise<void> {
  const db = await openDb();
  try {
    await tx(db, "readwrite", (store) => store.put(item));
  } finally {
    db.close();
  }
}

/** 全部待补传条目(按入队时间序) */
export async function listAll(): Promise<PendingAudio[]> {
  const db = await openDb();
  try {
    const items = await tx(db, "readonly", (store) => store.getAll() as IDBRequest<PendingAudio[]>);
    return items.sort((a, b) => a.queued_at - b.queued_at);
  } finally {
    db.close();
  }
}

/** 补传成功后移除 */
export async function remove(recordId: string): Promise<void> {
  const db = await openDb();
  try {
    await tx(db, "readwrite", (store) => store.delete(recordId));
  } finally {
    db.close();
  }
}

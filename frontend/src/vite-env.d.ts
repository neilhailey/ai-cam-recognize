/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_MAX_UPLOAD_FACES?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

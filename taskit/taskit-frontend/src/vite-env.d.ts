/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_HARNESS_TIME_API_URL: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}

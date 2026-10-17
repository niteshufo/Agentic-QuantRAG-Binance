import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

function runtimeReadModelPlugin() {
  const frontendDirectory = path.dirname(fileURLToPath(import.meta.url))
  const runtimeDirectory = path.resolve(frontendDirectory, '../data/runtime')
  return {
    name: 'runtime-read-model',
    configureServer(server) {
      server.middlewares.use('/api/runtime', async (request, response, next) => {
        const relativePath = request.url?.replace(/^\/+/, '') ?? ''
        if (!['latest_state.json', 'execution_records.jsonl'].includes(relativePath)) {
          next()
          return
        }
        try {
          const file = await fs.readFile(path.join(runtimeDirectory, relativePath))
          response.statusCode = 200
          response.setHeader('Cache-Control', 'no-store')
          response.setHeader(
            'Content-Type',
            relativePath.endsWith('.jsonl')
              ? 'application/x-ndjson; charset=utf-8'
              : 'application/json; charset=utf-8',
          )
          response.end(file)
        } catch (error) {
          if (error.code === 'ENOENT' || error.code === 'EBUSY') {
            response.statusCode = 404
            response.end('Runtime read model is not available yet.')
            return
          }
          next(error)
        }
      })
    },
  }
}

export default defineConfig({
  server: {
    fs: {
      strict: true,
    },
  },
  plugins: [react(), runtimeReadModelPlugin()],
})

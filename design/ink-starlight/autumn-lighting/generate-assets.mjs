#!/usr/bin/env node

// Project-local copy of the configured Sui-Xiang client.
// Only changes: 300s request budget and response-body timeout retry.
// Credential loading, destination and model routing are unchanged.
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const MODEL_ROUTES = {
  generate: ['gpt-image-2.5-flare', 'gpt-image-2.5-sunburst', 'gpt-image-2'],
  edit: ['gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-2']
}
const TRANSIENT_STATUSES = new Set([429, 502, 503, 504])
const MAX_INPUT_BYTES = 20 * 1024 * 1024
const DEFAULT_BASE_URL = 'https://www.sui-xiang.net/v1'
const SECRET_FILE = process.env.SUI_XIANG_SECRET_FILE || path.join(os.homedir(), '.codex/secrets/sui-xiang-imagegen.env')
const FORMAT_EXTENSIONS = { png: '.png', webp: '.webp', jpeg: '.jpg' }
const MIME_TYPES = { png: 'image/png', webp: 'image/webp', jpg: 'image/jpeg', jpeg: 'image/jpeg' }

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return {}
  const values = {}
  for (const line of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*(SUI_XIANG_API_KEY|SUI_XIANG_BASE_URL)\s*=\s*(.*?)\s*$/)
    if (!match) continue
    let value = match[2]
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1)
    values[match[1]] = value
  }
  return values
}

const fileConfig = parseEnvFile(SECRET_FILE)
const apiKey = process.env.SUI_XIANG_API_KEY || fileConfig.SUI_XIANG_API_KEY || process.env.IMAGE_API_KEY || process.env.OPENAI_API_KEY || ''
const rawBaseUrl = process.env.SUI_XIANG_BASE_URL || fileConfig.SUI_XIANG_BASE_URL || process.env.IMAGE_API_BASE_URL || DEFAULT_BASE_URL
const baseUrl = normalizeBaseUrl(rawBaseUrl)

function normalizeBaseUrl(value) {
  const parsed = new URL(value)
  if (!['https:', 'http:'].includes(parsed.protocol)) throw new Error('API base URL must use HTTPS or HTTP')
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('API base URL cannot contain credentials, query parameters, or a fragment')
  let normalized = parsed.toString().replace(/\/+$/, '')
  if (!normalized.endsWith('/v1')) normalized += '/v1'
  return normalized
}

function redact(value) {
  let text = String(value ?? '')
  if (apiKey) text = text.split(apiKey).join('[redacted]')
  return text.replace(/(authorization\s*[:=]\s*bearer\s+)[^\s,;]+/gi, '$1[redacted]').replace(/(bearer\s+)[A-Za-z0-9._~+/=-]+/gi, '$1[redacted]')
}

function parseArgs(argv) {
  const [command = 'help', ...rest] = argv
  const options = {}
  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index]
    if (!token.startsWith('--')) throw new Error('Unexpected argument: ' + token)
    const equalsAt = token.indexOf('=')
    const key = token.slice(2, equalsAt < 0 ? undefined : equalsAt)
    const value = equalsAt < 0 ? rest[index + 1] : token.slice(equalsAt + 1)
    if (key === 'help') {
      options.help = true
      continue
    }
    if (!value || (equalsAt < 0 && value.startsWith('--'))) throw new Error('Missing value for --' + key)
    if (equalsAt < 0) index += 1
    if (key === 'input') (options.inputs ||= []).push(value)
    else if (['prompt', 'size', 'quality', 'background', 'format', 'out', 'model', 'mask', 'manifest'].includes(key)) options[key] = value
    else throw new Error('Unknown option: --' + key)
  }
  return { command, options }
}

function help() {
  console.log('Usage:\n  node scripts/sui_xiang_image.mjs generate --prompt \"...\" [--size 1024x1024] [--quality low] [--background opaque] [--out file.png]\n  node scripts/sui_xiang_image.mjs edit --prompt \"...\" --input reference.png [--input reference2.png] [--mask mask.png] [--background transparent] [--out file.png]\n  node scripts/sui_xiang_image.mjs batch --manifest jobs.json\n  node scripts/sui_xiang_image.mjs doctor')
}

function validateSize(size) {
  const match = String(size || '').match(/^(\d{2,4})x(\d{2,4})$/)
  if (!match) throw new Error('Size must use WIDTHxHEIGHT, such as 1024x1024')
  const width = Number(match[1])
  const height = Number(match[2])
  if (width % 16 || height % 16 || width > 3840 || height > 2160 || width / height < 1 / 3 || width / height > 3) {
    throw new Error('Size must use dimensions divisible by 16, at most 3840x2160, with aspect ratio from 1:3 to 3:1')
  }
  return { width, height }
}

function validateJob(job, kind) {
  if (!job.prompt || !String(job.prompt).trim()) throw new Error('A non-empty --prompt is required')
  if (apiKey && String(job.prompt).includes(apiKey)) throw new Error('Prompt contains the configured API key; remove credentials from the prompt')
  const size = job.size || '1024x1024'
  validateSize(size)
  const quality = job.quality || 'low'
  if (!['auto', 'low', 'medium', 'high', 'xhigh', 'max'].includes(quality)) throw new Error('Unsupported quality: ' + quality)
  const background = job.background || 'opaque'
  if (!['auto', 'opaque', 'transparent'].includes(background)) throw new Error('Unsupported background: ' + background)
  const outExt = path.extname(job.out || '').toLowerCase().replace(/^\./, '')
  const format = (job.format || (outExt === 'jpg' ? 'jpeg' : outExt) || 'png').toLowerCase()
  if (!Object.hasOwn(FORMAT_EXTENSIONS, format)) throw new Error('Format must be png, webp, or jpeg')
  if (background === 'transparent' && !['png', 'webp'].includes(format)) throw new Error('Transparent output requires PNG or WebP')
  if (outExt && !(outExt === 'jpg' && format === 'jpeg') && outExt !== format) throw new Error('Output file extension and --format do not match')
  if (kind === 'edit' && (!job.inputs || job.inputs.length === 0)) throw new Error('Edit requires at least one --input')
  return { size, quality, background, format }
}

function hashFile(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex')
}

function resolveInput(filePath, baseDir = process.cwd()) {
  const resolved = path.isAbsolute(filePath) ? filePath : path.resolve(baseDir, filePath)
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) throw new Error('Input image not found: ' + resolved)
  const stat = fs.statSync(resolved)
  if (stat.size > MAX_INPUT_BYTES) throw new Error('Input image exceeds the 20 MiB gateway limit: ' + resolved)
  const ext = path.extname(resolved).slice(1).toLowerCase()
  if (!Object.hasOwn(MIME_TYPES, ext)) throw new Error('Input must be PNG, JPEG, or WebP: ' + resolved)
  return { path: resolved, size: stat.size, mime: MIME_TYPES[ext], sha256: hashFile(resolved) }
}

function slugify(text) {
  return String(text).normalize('NFKC').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'image'
}

function defaultOutputPath(prompt, format) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')
  return path.resolve(process.cwd(), 'output/imagegen', stamp + '-' + slugify(prompt) + FORMAT_EXTENSIONS[format])
}

function modelRoute(kind, requestedModel) {
  const route = MODEL_ROUTES[kind].slice()
  if (!requestedModel) return route
  if (!route.includes(requestedModel)) throw new Error('Unsupported model: ' + requestedModel)
  return [requestedModel, ...route.filter((model) => model !== requestedModel)]
}

function parseErrorBody(text) {
  try {
    const parsed = JSON.parse(text)
    return parsed.error?.message || parsed.message || JSON.stringify(parsed)
  } catch {
    return text
  }
}

function isModelCompatibilityError(status, message) {
  const text = String(message).toLowerCase()
  if ([401, 402, 403, 429].includes(status) || /insufficient|balance|billing|quota|content policy|moderation|safety system/.test(text)) return false
  return /model.{0,30}(not found|unavailable|does not exist|not available|unsupported|not supported)|(?:not found|unavailable|does not exist|not available|unsupported|unknown).{0,30}model|unsupported parameter|unknown parameter|parameter.{0,20}(not supported|unsupported)|does not support.{0,30}(quality|background|size|output_format)|(background|quality|size|output_format).{0,30}(not supported|unsupported)/.test(text)
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function sendRequest(kind, model, bodyFactory) {
  const endpoint = baseUrl + (kind === 'generate' ? '/images/generations' : '/images/edits')
  for (let attempt = 0; attempt <= 3; attempt += 1) {
    let response
    try {
      const body = bodyFactory(model)
      response = await fetch(endpoint, {
        method: 'POST',
        headers: { Authorization: 'Bearer ' + apiKey, ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }) },
        body: body instanceof FormData ? body : JSON.stringify(body),
        signal: AbortSignal.timeout(300000)
      })
    } catch (error) {
      if (attempt < 3) {
        await sleep(500 * (2 ** attempt))
        continue
      }
      throw new Error('Image API network/timeout failure after retries: ' + redact(error.message))
    }
    let responseText
    try {
      responseText = await response.text()
    } catch (error) {
      if (attempt < 3) {
        await sleep(500 * (2 ** attempt))
        continue
      }
      throw new Error('Image response body timeout after retries: ' + redact(error.message))
    }
    if (response.ok) {
      try {
        return { payload: JSON.parse(responseText), model }
      } catch {
        throw new Error('Image API returned invalid JSON')
      }
    }
    const message = redact(parseErrorBody(responseText))
    if (TRANSIENT_STATUSES.has(response.status) && attempt < 3) {
      await sleep(500 * (2 ** attempt))
      continue
    }
    const error = new Error('Image API returned HTTP ' + response.status + ': ' + message)
    error.status = response.status
    error.providerMessage = message
    throw error
  }
  throw new Error('Image API request failed')
}

async function requestWithFallback(kind, options, references, mask) {
  let lastError
  for (const model of modelRoute(kind, options.model)) {
    try {
      return await sendRequest(kind, model, (selectedModel) => {
        if (kind === 'generate') {
          return {
            model: selectedModel,
            prompt: options.prompt,
            size: options.size,
            quality: options.quality,
            background: options.background,
            output_format: options.format
          }
        }
        const form = new FormData()
        form.append('model', selectedModel)
        form.append('prompt', options.prompt)
        form.append('size', options.size)
        form.append('quality', options.quality)
        form.append('background', options.background)
        form.append('output_format', options.format)
        for (const reference of references) {
          form.append('image[]', new Blob([fs.readFileSync(reference.path)], { type: reference.mime }), path.basename(reference.path))
        }
        if (mask) form.append('mask', new Blob([fs.readFileSync(mask.path)], { type: mask.mime }), path.basename(mask.path))
        return form
      })
    } catch (error) {
      lastError = error
      if (!isModelCompatibilityError(error.status, error.providerMessage || error.message)) throw error
    }
  }
  throw lastError || new Error('No image model was available')
}

async function imageBufferFromPayload(payload) {
  const entry = payload.data?.[0]
  if (!entry) throw new Error('Image API response contains no image')
  if (entry.b64_json) return Buffer.from(entry.b64_json.replace(/^data:image\/[^;]+;base64,/, ''), 'base64')
  if (entry.url) {
    for (let attempt = 0; attempt <= 3; attempt += 1) {
      let response
      try {
        response = await fetch(entry.url, { signal: AbortSignal.timeout(60000) })
      } catch (error) {
        if (attempt === 3) throw new Error('Generated image download failed after retries: ' + redact(error.message))
        await sleep(500 * (2 ** attempt))
        continue
      }
      if (response.ok) return Buffer.from(await response.arrayBuffer())
      if (!TRANSIENT_STATUSES.has(response.status) || attempt === 3) throw new Error('Generated image URL returned HTTP ' + response.status)
      await sleep(500 * (2 ** attempt))
    }
  }
  throw new Error('Image API response has neither b64_json nor url')
}

function detectFormat(buffer) {
  if (buffer.length >= 8 && buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return 'png'
  if (buffer.length >= 12 && buffer.toString('ascii', 0, 4) === 'RIFF' && buffer.toString('ascii', 8, 12) === 'WEBP') return 'webp'
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) return 'jpeg'
  return ''
}

function imageDimensions(buffer, format) {
  if (format === 'png' && buffer.length >= 33 && buffer.toString('ascii', 12, 16) === 'IHDR') {
    const dimensions = { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) }
    return dimensions.width && dimensions.height ? dimensions : null
  }
  if (format === 'webp' && buffer.length >= 30 && buffer.toString('ascii', 12, 16) === 'VP8X') {
    return { width: 1 + buffer[24] + (buffer[25] << 8) + (buffer[26] << 16), height: 1 + buffer[27] + (buffer[28] << 8) + (buffer[29] << 16) }
  }
  if (format === 'jpeg') {
    let offset = 2
    while (offset + 9 < buffer.length) {
      if (buffer[offset] !== 0xff) break
      const marker = buffer[offset + 1]
      const length = buffer.readUInt16BE(offset + 2)
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        return { width: buffer.readUInt16BE(offset + 7), height: buffer.readUInt16BE(offset + 5) }
      }
      offset += 2 + length
    }
  }
  return null
}

function versionedPath(filePath, version) {
  if (version === 1) return filePath
  const extension = path.extname(filePath)
  if (!extension) return filePath + '-v' + version
  return filePath.slice(0, -extension.length) + '-v' + version + extension
}

function saveNoOverwrite(filePath, buffer, metadata) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  for (let version = 1; version < 1000; version += 1) {
    const candidate = versionedPath(filePath, version)
    const metadataPath = candidate + '.metadata.json'
    if (fs.existsSync(candidate) || fs.existsSync(metadataPath)) continue
    let descriptor
    let createdCandidate = false
    try {
      descriptor = fs.openSync(candidate, 'wx', 0o600)
      createdCandidate = true
      fs.writeFileSync(descriptor, buffer)
      fs.closeSync(descriptor)
      descriptor = undefined
      fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
      return { outputPath: candidate, metadataPath }
    } catch (error) {
      if (descriptor !== undefined) fs.closeSync(descriptor)
      if (createdCandidate && fs.existsSync(candidate)) fs.rmSync(candidate, { force: true })
      if (error.code === 'EEXIST') continue
      throw error
    }
  }
  throw new Error('Could not select a free output filename')
}

async function runImageJob(kind, job, baseDir = process.cwd()) {
  if (!apiKey) throw new Error('Missing Sui-Xiang API key. Run scripts/configure_secret.sh in a terminal to configure it.')
  const validated = validateJob(job, kind)
  const references = (job.inputs || []).map((input) => resolveInput(input, baseDir))
  const mask = job.mask ? resolveInput(job.mask, baseDir) : null
  const startedAt = Date.now()
  const { payload, model } = await requestWithFallback(kind, { ...job, ...validated }, references, mask)
  const buffer = await imageBufferFromPayload(payload)
  const detectedFormat = detectFormat(buffer)
  if (!detectedFormat) throw new Error('Response body is not a recognized PNG, WebP, or JPEG image')
  if (detectedFormat !== validated.format) throw new Error('Requested ' + validated.format + ' but gateway returned ' + detectedFormat)
  const actualDimensions = imageDimensions(buffer, detectedFormat)
  let outputPath = job.out
    ? (path.isAbsolute(job.out) ? job.out : path.resolve(baseDir, job.out))
    : defaultOutputPath(job.prompt, validated.format)
  if (!path.extname(outputPath)) outputPath += FORMAT_EXTENSIONS[validated.format]
  if ((detectedFormat === 'png' || detectedFormat === 'jpeg') && !actualDimensions) throw new Error('Gateway returned an invalid or truncated ' + detectedFormat.toUpperCase() + ' image')
  const metadata = {
    provider: 'sui-xiang',
    endpoint: baseUrl,
    model: payload.model || model,
    operation: kind,
    prompt: redact(job.prompt),
    quality: validated.quality,
    size: validated.size,
    background: validated.background,
    format: validated.format,
    dimensions: actualDimensions,
    elapsedMs: Date.now() - startedAt,
    usage: payload.usage || null,
    references: references.map(({ path: inputPath, sha256, size }) => ({ path: redact(inputPath), sha256, size })),
    mask: mask ? { path: redact(mask.path), sha256: mask.sha256, size: mask.size } : null,
    createdAt: new Date().toISOString()
  }
  const saved = saveNoOverwrite(outputPath, buffer, metadata)
  console.log('Saved ' + saved.outputPath + ' (' + detectedFormat + (actualDimensions ? ', ' + actualDimensions.width + 'x' + actualDimensions.height : '') + ', ' + (payload.model || model) + ')')
  console.log('Metadata ' + saved.metadataPath)
  return { ...saved, model: payload.model || model, dimensions: actualDimensions }
}

function doctor() {
  console.log('Sui-Xiang base URL: ' + baseUrl)
  const source = process.env.SUI_XIANG_API_KEY ? 'SUI_XIANG_API_KEY environment variable' : fileConfig.SUI_XIANG_API_KEY ? SECRET_FILE : process.env.IMAGE_API_KEY ? 'legacy IMAGE_API_KEY environment variable' : process.env.OPENAI_API_KEY ? 'legacy OPENAI_API_KEY environment variable' : 'not configured'
  console.log('Credential source: ' + source)
  console.log('Credential value: ' + (apiKey ? 'present (hidden)' : 'missing'))
  if (!apiKey) {
    console.log('Configure a rotated key by running: bash ' + path.join(path.dirname(fileURLToPath(import.meta.url)), 'configure_secret.sh'))
    process.exitCode = 2
  }
}

async function runBatch(manifestPath) {
  if (!manifestPath) throw new Error('batch requires --manifest')
  const resolvedManifest = path.resolve(manifestPath)
  const document = JSON.parse(fs.readFileSync(resolvedManifest, 'utf8'))
  const jobs = Array.isArray(document) ? document : document.jobs
  if (!Array.isArray(jobs)) throw new Error('Batch manifest must be an array or an object containing jobs')
  const defaults = Array.isArray(document) ? {} : (document.defaults || {})
  const failures = []
  const baseDir = path.dirname(resolvedManifest)
  for (let index = 0; index < jobs.length; index += 1) {
    const job = { ...defaults, ...jobs[index] }
    const kind = job.kind
    if (!['generate', 'edit'].includes(kind)) {
      failures.push({ index, id: job.id || null, error: 'Job kind must be generate or edit' })
      continue
    }
    try {
      const normalized = { ...job, inputs: job.inputs || (job.input ? [job.input] : []) }
      await runImageJob(kind, normalized, baseDir)
    } catch (error) {
      const message = redact(error.message)
      console.error('Batch job ' + (job.id || index + 1) + ' failed: ' + message)
      failures.push({ index, id: job.id || null, error: message })
    }
  }
  if (failures.length) {
    const failurePath = resolvedManifest + '.failures-' + Date.now() + '.json'
    fs.writeFileSync(failurePath, JSON.stringify({ failures }, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
    console.error(failures.length + ' job(s) failed; completed outputs were kept. Failure list: ' + failurePath)
    process.exitCode = 1
  } else {
    console.log('Batch completed: ' + jobs.length + ' job(s)')
  }
}

async function main() {
  if (process.argv[2] === '--help' || process.argv[2] === '-h') return help()
  const { command, options } = parseArgs(process.argv.slice(2))
  if (options.help || command === 'help') return help()
  if (command === 'doctor') return doctor()
  if (command === 'batch') return runBatch(options.manifest)
  if (command !== 'generate' && command !== 'edit') throw new Error('Unknown command: ' + command)
  await runImageJob(command, { ...options, inputs: options.inputs || [] })
}

main().catch((error) => {
  console.error(redact(error.message))
  process.exitCode = 1
})

/**
 * Hermes Companion Pair — desktop sidebar page (v2).
 *
 * Mirrors the web dashboard tab's pairing UI (dashboard/dist/index.js)
 * natively inside the Hermes desktop app, so pairing is one sidebar
 * entry away instead of a dashboard URL or a CLI command.
 *
 * Mechanism (proven by the peer plugin PIR8-Software/Hermes-OR-Picker,
 * catalog/desktop/plugin.js): a JS module using '@hermes/plugin-sdk'
 * that registers a ROUTES_AREA page, a SIDEBAR_NAV_AREA entry, and a
 * PALETTE_AREA command via ctx.registerMany. Backend calls go through
 * ctx.rest with plugin-relative paths (OR Picker's convention: '/models',
 * '/credits', …); here the same helper reaches this plugin's existing
 * dashboard/plugin_api.py routes ('/offers', '/devices', …).
 *
 * Discovery (from OR Picker's install.sh — read it, do not assume):
 * the desktop app loads desktop plugins from
 * $HERMES_HOME/desktop-plugins/<plugin-id>/plugin.js, NOT from the
 * plugin directory installed by `hermes plugins install`. Activating
 * this page therefore needs one copy plus a reload:
 *   mkdir -p ~/.hermes/desktop-plugins/hermes-companion-pair
 *   cp desktop/plugin.js ~/.hermes/desktop-plugins/hermes-companion-pair/plugin.js
 *   # then in the desktop app: Cmd/Ctrl+K → Reload desktop plugins
 * Whether the desktop app ALSO auto-discovers a desktop/ entry inside
 * the installed plugin directory is UNCONFIRMED; the copy above is the
 * confirmed path. `hermes plugins update` alone does NOT install the
 * sidebar page.
 *
 * QR rendering — strict square, by construction (the web tab's QR is
 * currently unscannable: screenshot evidence showed a ~12%-stretched
 * region and inverted colors versus the black-on-white SVG the backend
 * generates). This page never lets a container decide the QR's shape:
 * the SVG gets equal width/height attributes AND equal CSS dimensions,
 * preserveAspectRatio is pinned, it sits in a fixed square box on a
 * white background, and no CSS filter is applied anywhere near it.
 * The backend SVG already carries a square viewBox and a white
 * background rect (companion_pairing/qr.py: matrix_to_svg); this page
 * only enforces the display geometry.
 *
 * Secrets: this page displays the QR payload the owner just created
 * (same as the web tab) and nothing else. No secret, credential, or
 * token value is ever logged; device rows show public fields only.
 */

import {
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, host,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState, useEffect, useCallback, useRef } from 'react'

const ID = 'hermes-companion-pair'
const PAGE = '/companion-pair'
const QR_SIZE = 320

async function api(ctx, method, path, body) {
  const opts = { method, timeoutMs: 30000 }
  if (body !== undefined) opts.body = body
  return ctx.rest(path, opts)
}

function injectSquareQr(box, svg) {
  if (!box) return
  box.innerHTML = svg || ''
  const svgEl = box.querySelector('svg')
  if (!svgEl) return
  svgEl.setAttribute('width', String(QR_SIZE))
  svgEl.setAttribute('height', String(QR_SIZE))
  svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet')
  svgEl.style.display = 'block'
  svgEl.style.width = QR_SIZE + 'px'
  svgEl.style.height = QR_SIZE + 'px'
  svgEl.style.filter = 'none'
}

function CompanionPage({ ctx }) {
  const [statusLine, setStatusLine] = useState('Checking dashboard status…')
  const [devices, setDevices] = useState([])
  const [error, setError] = useState(null)
  const [offer, setOffer] = useState(null)
  const [countdown, setCountdown] = useState('')
  const [pairedMsg, setPairedMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const qrBoxRef = useRef(null)
  const knownIdsRef = useRef({})

  const loadDevices = useCallback(async () => {
    const data = await api(ctx, 'GET', '/devices')
    const list = data.devices || []
    setDevices(list)
    return list
  }, [ctx])

  const refreshStatus = useCallback(async () => {
    try {
      const status = await api(ctx, 'GET', '/dashboard-status')
      setStatusLine(
        'Dashboard: ' + (status.reachable ? 'running' : 'not reachable') +
        ' (' + status.host + ':' + status.port + ')' +
        ' · Auto-start: ' + (status.autostart_disabled ? 'off' : 'on')
      )
    } catch {
      setStatusLine('Dashboard status unavailable.')
    }
  }, [ctx])

  useEffect(() => {
    refreshStatus()
    loadDevices().catch((err) => {
      setError('Could not load devices: ' + err.message)
    })
  }, [refreshStatus, loadDevices])

  useEffect(() => {
    if (!offer) return undefined
    injectSquareQr(qrBoxRef.current, offer.qrSvg)
    const tick = setInterval(() => {
      const left = offer.expiresAt - Math.floor(Date.now() / 1000)
      if (left <= 0) {
        setOffer(null)
        setCountdown('')
        return
      }
      setCountdown('Expires in ' + left + 's')
    }, 1000)
    setCountdown('Expires in 300s')
    const poll = setInterval(() => {
      loadDevices().then((list) => {
        const fresh = list.filter((d) => !knownIdsRef.current[d.id])
        if (fresh.length > 0) {
          setOffer(null)
          setCountdown('')
          setPairedMsg('Paired: ' + (fresh[0].name || fresh[0].id))
        }
      }).catch(() => {})
    }, 2000)
    return () => {
      clearInterval(tick)
      clearInterval(poll)
    }
  }, [offer, loadDevices])

  const handlePair = async () => {
    setError(null)
    setPairedMsg(null)
    setBusy(true)
    try {
      const data = await api(ctx, 'POST', '/offers')
      setOffer({
        qrSvg: data.qr_svg || '',
        payloadText: JSON.stringify(data.payload, null, 2),
        expiresAt: data.expires_at || 0,
        bindWarning: data.bind_warning || null,
      })
      const list = await loadDevices()
      const known = {}
      list.forEach((d) => { known[d.id] = true })
      knownIdsRef.current = known
    } catch (err) {
      setError('Could not create an offer: ' + err.message)
    } finally {
      setBusy(false)
    }
  }

  const handleRevoke = async (dev) => {
    if (!window.confirm('Revoke ' + (dev.name || dev.id) + '? Its credential will stop working.')) return
    setError(null)
    try {
      await api(ctx, 'DELETE', '/devices/' + encodeURIComponent(dev.id))
      await loadDevices()
    } catch (err) {
      setError('Revoke failed: ' + err.message)
    }
  }

  const handleCopy = async () => {
    const text = offer ? offer.payloadText : ''
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy') } catch (e) {}
      document.body.removeChild(ta)
    }
  }

  const rowStyle = {
    display: 'flex', gap: 12, alignItems: 'center',
    borderBottom: '1px solid var(--color-border, #444)', padding: '8px 0',
  }
  const btnStyle = {
    padding: '8px 16px', borderRadius: 8, cursor: 'pointer',
    border: '1px solid var(--color-border, #444)',
    background: 'var(--color-primary, #2a6)', color: 'var(--color-primary-foreground, #fff)',
  }

  return jsxs('div', {
    style: { maxWidth: 720, margin: '0 auto', padding: 16 },
    children: [
      jsx('h2', { style: { fontSize: '1.4em', marginBottom: 4 }, children: 'Companion' }),
      jsx('p', {
        style: { color: 'var(--color-muted-foreground, #888)', marginBottom: 12 },
        children: 'Pair a phone with one QR scan. The code works once and expires in 5 minutes. '
          + 'Revoke a device any time — its credential stops working immediately.',
      }),
      jsx('p', { style: { fontSize: '0.9em', color: 'var(--color-muted-foreground, #888)' }, children: statusLine }),
      error ? jsx('div', {
        style: { background: 'rgba(200, 60, 60, 0.15)', border: '1px solid #c33', padding: '8px 12px', borderRadius: 8, margin: '12px 0' },
        children: error,
      }) : null,
      pairedMsg ? jsx('div', {
        style: { background: 'rgba(60, 180, 90, 0.15)', border: '1px solid #3b5', padding: '8px 12px', borderRadius: 8, margin: '12px 0' },
        children: pairedMsg,
      }) : null,
      jsx('button', {
        style: btnStyle, disabled: busy, onClick: handlePair,
        children: 'Pair new device',
      }),
      offer ? jsxs('div', {
        style: { border: '1px solid var(--color-border, #444)', borderRadius: 12, padding: 16, margin: '16px 0' },
        children: [
          jsx('div', {
            ref: qrBoxRef,
            style: {
              width: QR_SIZE + 'px', height: QR_SIZE + 'px',
              background: '#fff', padding: 8, borderRadius: 8,
            },
          }),
          jsx('p', { style: { fontWeight: 'bold' }, children: countdown }),
          offer.bindWarning ? jsx('p', {
            style: { background: 'rgba(220, 160, 0, 0.15)', border: '1px solid #da0', padding: '8px 12px', borderRadius: 8 },
            children: offer.bindWarning,
          }) : null,
          jsx('pre', {
            style: {
              whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontSize: '0.8em',
              background: 'rgba(128, 128, 128, 0.12)', padding: 12, borderRadius: 8,
            },
            children: offer.payloadText,
          }),
          jsx('button', {
            style: { ...btnStyle, background: 'transparent', color: 'inherit', marginRight: 8 },
            onClick: handleCopy, children: 'Copy payload',
          }),
          jsx('button', {
            style: { ...btnStyle, background: 'transparent', color: 'inherit' },
            onClick: () => { setOffer(null); setCountdown('') },
            children: 'Close',
          }),
        ],
      }) : null,
      jsx('h3', { style: { marginTop: 24 }, children: 'Paired devices' }),
      devices.length === 0
        ? jsx('p', { style: { color: 'var(--color-muted-foreground, #888)' }, children: 'No devices paired yet.' })
        : jsxs('div', {
            children: devices.map((dev) => jsxs('div', {
              style: rowStyle,
              children: [
                jsx('span', { style: { fontWeight: 'bold' }, children: dev.name || dev.id }),
                jsx('span', {
                  style: { flex: 1, fontSize: '0.85em', color: 'var(--color-muted-foreground, #888)' },
                  children: 'paired ' + (dev.paired_at ? new Date(dev.paired_at * 1000).toLocaleString() : '—')
                    + ' · ' + (dev.credential_state || 'active'),
                }),
                dev.credential_state !== 'revoked' ? jsx('button', {
                  style: { ...btnStyle, background: 'transparent', color: 'var(--color-destructive, #c33)' },
                  onClick: () => handleRevoke(dev),
                  children: 'Revoke',
                }) : null,
              ],
            }, dev.id)),
          }),
    ],
  })
}

export default {
  id: ID,
  name: 'Companion',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: PAGE },
        render: () => jsx(CompanionPage, { ctx }),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: PAGE, label: 'Companion', codicon: 'key' },
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: ID + '.open',
          label: 'Open Companion Pairing',
          keywords: ['companion', 'pair', 'pairing', 'phone', 'qr'],
          run: () => host.navigate(PAGE),
        },
      },
    ])
  },
}

import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { applySurfaceAttribute } from './lib/surface'
import './index.css'

// Mirror ?surface= onto <html> before first paint so the overlay never paints
// an opaque canvas for even one frame.
applySurfaceAttribute()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

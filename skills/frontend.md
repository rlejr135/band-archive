---
name: Frontend Architecture
description: Analysis of the frontend architecture, structure, and functionality
---

# Frontend Analysis - Band Archive

## Overview
The frontend is a **React 19** application built with **Vite 7**, designed to manage a band's archive. It features song management, practice logs, member tracking, and song suggestions.

## Technology Stack
- **Framework**: React 19.2.0
- **Routing**: React Router DOM 7.13.0
- **Build Tool**: Vite 7.2.4
- **State Management**: React Context API (`SongContext`)
- **Styling**: Vanilla CSS (`App.css`, `index.css`, and modular CSS)
- **HTTP Client**: Native `fetch` API & `XMLHttpRequest` (for progress tracking)

## Project Structure
`band-archive/frontend/src/`

- **`components/`**: Modular UI components
    - `dashboard/`: Dashboard view components
    - `songs/`: Song list, details, forms, and suggestion components
    - `members/`: Member list and detail views
    - `common/`: Reusable components (e.g., `SearchBar`)
    - `layout/`: Layout components (e.g., Header)
    - `practices/`: Practice log components
- **`context/`**: Global state management
    - `SongContext.jsx`: Manages song lists, current selection, and loading states.
- **`services/`**: API interaction layers
    - `api.js`: Core API functions for songs, logs, and suggestions.
    - `memberApi.js`: API functions specific to member management.
- **`hooks/`**: Custom hooks (e.g., likely for data fetching or form handling).
- **`App.jsx`**: Main application component handling routing and layout.

## Key Features & Routes

### 1. Dashboard (`/`)
- Displays an overview of band activities.
- Shows statistics (via `fetchDashboardStats`).
- Entry point for quick navigation.

### 2. Song Management (`/songs`)
- **List View**: Browse all songs with search/filter capabilities.
- **Detail View (`/songs/:id`)**: 
    - View song details (lyrics, keys, etc.).
    - Manage associated media (audio/scores).
    - CRUD operations for songs.
- **Practice Logs**: 
    - Record practice sessions per song.
    - Upload recordings of practice sessions.

### 3. Member Management (`/members`)
- **Directory**: List all band members.
- **Profiles (`/members/:id`)**: 
    - Individual member details.
    - Personal logs (upload & manage personal practice files).

### 4. Suggestions (`/suggestions`)
- Submit new song ideas.
- Vote on suggested songs.
- Secure deletion (password protected).

## API Integration (`src/services`)
- **`api.js`**:
    - `fetchSongs`, `getSong`, `createSong`, `updateSong`, `deleteSong`
    - `uploadMedia` (supports progress tracking via XHR)
    - `fetchPracticeLogs`, `createPracticeLog`, `uploadRecording`
    - `fetchSuggestions`, `voteSuggestion`
- **`memberApi.js`**:
    - CRUD for members.
    - `uploadPersonalLog` (supports progress tracking).

## Development Commands
- `npm run dev`: Start development server (Vite)
- `npm run build`: Build for production
- `npm run lint`: Run ESLint
- `npm run preview`: Preview production build

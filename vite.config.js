// Used by the development server (npm run dev) and by the build. Only the interface sources under src/ need watching.
// Watching build outputs, tool folders and the big test films costs CPU and, on Windows, the watcher's directory
// handles stop Explorer from deleting or renaming those folders (a freshly unpacked portable copy inside the project).
export default {
  server: {
    watch: {
      ignored: ['**/dist/**', '**/dist-portable/**', '**/.tools/**', '**/.runtime/**', '**/.local/**', '**/.npm-cache/**', '**/src-tauri/**', '**/backend/**', '**/scripts/**', '**/tests/**'],
    },
  },
};

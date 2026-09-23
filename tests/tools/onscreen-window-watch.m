// Copyright 2026 Browser CLI contributors
// Continuous on-screen window watcher for the signed Browser CLI app.
//
// Unlike tests/tools/onscreen-window-count.m (which needs a PID and is started
// after Popen, leaving a spawn-time sampling blind spot), this watcher is
// started BEFORE any launch and matches windows by **app identity**: for every
// on-screen window it resolves the owner process's executable path via
// proc_pidpath() and counts it only when it equals the expected app binary
// path. It keeps sampling across the whole timeline (spawn, run, exit, crash,
// recovery) until SIGTERM/SIGINT.
//
// Output (append mode, flushed every sample):
//   sample ts=<epoch_ms> count=<n>
//   detail ts=<epoch_ms> pid=<pid> bounds=<x,y,w,h>   (only for matching windows)
//   final max=<n> nonzero_samples=<n>
//
// No screenshot APIs are used; no window titles are read (avoids any screen
// recording permission surface). Compiled on demand by the test driver.
//
// Sampling interval defaults to 10ms (the acceptance gate requires <=20ms so a
// sub-100ms flash cannot hide between samples). The driver records the actual
// interval and the sample count in the evidence.
//
// Usage: onscreen-window-watch <app-binary-path> <samples-file> [interval-ms]

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

#include <libproc.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig) {
  (void)sig;
  g_stop = 1;
}

static long long now_ms(void) {
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int main(int argc, const char* argv[]) {
  @autoreleasepool {
    if (argc < 3) {
      fprintf(stderr,
              "usage: %s <app-binary-path> <samples-file> [interval-ms]\n",
              argv[0]);
      return 2;
    }
    char expected[PROC_PIDPATHINFO_MAXSIZE];
    if (realpath(argv[1], expected) == NULL) {
      fprintf(stderr, "bad app path: %s\n", argv[1]);
      return 2;
    }
    const char* samples_path = argv[2];
    int interval_ms = argc > 3 ? atoi(argv[3]) : 10;
    if (interval_ms <= 0) {
      interval_ms = 10;
    }

    FILE* out = fopen(samples_path, "a");
    if (out == NULL) {
      fprintf(stderr, "cannot open samples file: %s\n", samples_path);
      return 2;
    }

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    int max_count = 0;
    int nonzero_samples = 0;
    while (!g_stop) {
      int count = 0;
      CFArrayRef list = CGWindowListCopyWindowInfo(
          kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
          kCGNullWindowID);
      if (list != NULL) {
        CFIndex n = CFArrayGetCount(list);
        for (CFIndex i = 0; i < n; i++) {
          CFDictionaryRef info = (CFDictionaryRef)CFArrayGetValueAtIndex(list, i);
          CFNumberRef pid_ref =
              (CFNumberRef)CFDictionaryGetValue(info, kCGWindowOwnerPID);
          int pid = -1;
          if (pid_ref != NULL) {
            CFNumberGetValue(pid_ref, kCFNumberIntType, &pid);
          }
          if (pid <= 0) {
            continue;
          }
          char path[PROC_PIDPATHINFO_MAXSIZE];
          if (proc_pidpath(pid, path, sizeof(path)) <= 0) {
            continue;
          }
          if (strcmp(path, expected) != 0) {
            continue;
          }
          count++;
          CGRect bounds = CGRectZero;
          CFDictionaryRef bounds_ref =
              (CFDictionaryRef)CFDictionaryGetValue(info, kCGWindowBounds);
          if (bounds_ref != NULL) {
            CGRectMakeWithDictionaryRepresentation(bounds_ref, &bounds);
          }
          fprintf(out, "detail ts=%lld pid=%d bounds=%.0f,%.0f,%.0f,%.0f\n",
                  now_ms(), pid, bounds.origin.x, bounds.origin.y,
                  bounds.size.width, bounds.size.height);
        }
        CFRelease(list);
      }
      fprintf(out, "sample ts=%lld count=%d\n", now_ms(), count);
      fflush(out);
      if (count > max_count) {
        max_count = count;
      }
      if (count > 0) {
        nonzero_samples++;
      }
      usleep((useconds_t)interval_ms * 1000);
    }

    fprintf(out, "final max=%d nonzero_samples=%d\n", max_count,
            nonzero_samples);
    fflush(out);
    fclose(out);
  }
  return 0;
}

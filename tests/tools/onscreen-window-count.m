// Copyright 2026 Browser CLI contributors
// Minimal CoreGraphics helper: count on-screen windows owned by a PID.
//
// Used by tests/phase2-background-presentation-smoke.sh to prove that a
// BACKGROUND_PRESENTATION window never reaches the desktop. Compiled on demand
// by the smoke script (no build-system integration).

#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, const char* argv[]) {
  @autoreleasepool {
    if (argc < 2) {
      fprintf(stderr, "usage: %s <pid>\n", argv[0]);
      return 2;
    }
    int pid = atoi(argv[1]);

    CFArrayRef list = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID);
    if (!list) {
      fprintf(stderr, "CGWindowListCopyWindowInfo failed\n");
      return 3;
    }

    int count = 0;
    CFIndex n = CFArrayGetCount(list);
    for (CFIndex i = 0; i < n; i++) {
      CFDictionaryRef info = (CFDictionaryRef)CFArrayGetValueAtIndex(list, i);
      CFNumberRef pid_ref =
          (CFNumberRef)CFDictionaryGetValue(info, kCGWindowOwnerPID);
      int window_pid = -1;
      if (pid_ref) {
        CFNumberGetValue(pid_ref, kCFNumberIntType, &window_pid);
      }
      if (window_pid == pid) {
        count++;
      }
    }
    printf("%d\n", count);
    CFRelease(list);
  }
  return 0;
}

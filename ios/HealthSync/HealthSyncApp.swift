import SwiftUI

@main
struct HealthSyncApp: App {
    @AppStorage("authToken") private var authToken: String = ""

    var body: some Scene {
        WindowGroup {
            if authToken.isEmpty {
                LoginView()
            } else {
                TabView {
                    DashboardView()
                        .tabItem { Label("Today", systemImage: "heart.text.square.fill") }
                    CoachView()
                        .tabItem { Label("Coach", systemImage: "bubble.left.and.text.bubble.right.fill") }
                }
            }
        }
    }
}

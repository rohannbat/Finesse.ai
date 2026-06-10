import SwiftUI

@main
struct HealthSyncApp: App {
    @AppStorage("authToken") private var authToken: String = ""

    var body: some Scene {
        WindowGroup {
            if authToken.isEmpty {
                LoginView()
            } else {
                DashboardView()
            }
        }
    }
}

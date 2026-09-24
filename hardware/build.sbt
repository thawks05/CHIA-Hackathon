ThisBuild / scalaVersion := "2.13.18"
ThisBuild / version := "0.1.0"
ThisBuild / organization := "org.pivot"

// Versions match the official chipsalliance/chisel-template combination.
val chiselVersion = "7.7.0"
lazy val root = (project in file(".")).settings(
  name := "pivot-tile",
  libraryDependencies += "org.chipsalliance" %% "chisel" % chiselVersion,
  addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chiselVersion cross CrossVersion.full),
  scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked", "-language:reflectiveCalls")
)

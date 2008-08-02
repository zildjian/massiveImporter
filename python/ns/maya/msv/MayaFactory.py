# The MIT License
#	
# Copyright (c) 2008 James Piechota
#	
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import sys
from copy import deepcopy

import maya.cmds as mc
import maya.mel
from maya.OpenMaya import *

import ns.py as npy
import ns.py.Errors

import ns.maya.msv as nmsv
import ns.bridge.data.AgentSpec as AgentSpec
import ns.maya.msv.MayaAgent as MayaAgent
import ns.maya.msv.MayaSkin as MayaSkin
import ns.maya.msv.MayaUtil as MayaUtil

class MayaFactory:
	def __init__(self):
		self.geoMasters = {}
		self.clusterCache = {}
		self.primitiveCache = {}
		self.materialCache = {}
		self._geoMastersGroup = ""
		self._primitiveCacheGroup = ""
		self._primitivesInstanced = False
	
	def _getGeoMastersGroup(self):
		if not self._geoMastersGroup:
			self._geoMastersGroup = mc.group(empty=True, name="geometryMasters")
			self._geoMastersGroup = '|%s' % self._geoMastersGroup
		return self._geoMastersGroup
	
	def _getPrimitiveCacheGroup( self ):		
		if not self._primitiveCacheGroup:
			self._primitiveCacheGroup = mc.group(empty=True, name="msvPrimitives")
			self._primitiveCacheGroup = '|%s' % self._primitiveCacheGroup
			mc.setAttr( "%s.visibility" % self._primitiveCacheGroup, False )
		return self._primitiveCacheGroup
	
	def importObj(self, file, groupName):
		'''Import and prep an obj file. This is used to import the terrain
		   object as well as the agent geometry objects.'''
		print >> sys.stderr, "file -import"
		print >> sys.stderr, "groupName: %s" % groupName
		importedNodes = mc.file(file, returnNewNodes=True, type="OBJ", options='mo=0',
								groupName=groupName, groupReference=True, i=True)
		print >> sys.stderr, "file -import DONE"
		meshes = mc.ls(importedNodes, long=True, type="mesh")
		for mesh in meshes:
			mc.polySoftEdge(mesh, a=180, ch=False)
		[newGroup] = mc.ls(importedNodes, long=True, assemblies=True)
		return newGroup
	
	def importGeometry(self, mayaGeometry, groupName, skinType):
		'''Build Maya geometry either by importing it from disk, or copying
		   an already imported geometry "master".'''
		key = ""
		if MayaSkin.eSkinType.smooth == skinType:
			# When smooth binding every agent just gets a copy of the
			# .obj file geometry - so we can key the cache on the file
			# name and ignore the agent type
			#
			key = mayaGeometry.file()
		else:
			# When we are chunk binding, each agent type may chunk up the
			# geometry differently so we have to have one cache entry per
			# .obj file agent type combination
			#
			key = "%s_%s" % (mayaGeometry.mayaAgent.agentType(), mayaGeometry.file())
		if key in self.geoMasters:
			# "master" exists, copy it.
			mayaGeometry.skin = self.geoMasters[key].copy( groupName, mayaGeometry.skinnable() )
		else:
			print >> sys.stderr,  "importGeometry %s" % (mayaGeometry.file())
			# no "master", create one, and then copy it.
			skin = MayaSkin.MayaSkin(group=self.importObj(mayaGeometry.file(), groupName),
									 name=groupName,
									 parent=self._getGeoMastersGroup(),
									 skinType=skinType)
			print >> sys.stderr, "done importGeometry"
	 		#regulator = mc.createNode( "msvMeshRegulator", name="regulator", skipSelect=True )
	 		#mc.connectAttr( "%s.outMesh" % skin.shapeName(), "%s.inMesh" % regulator )
			if ( not mayaGeometry.attached() and
				 MayaSkin.eSkinType.smooth != skinType ):
				# chunked skinning is needed so break up the geometry
				skin.createChunks(mayaGeometry.weights(), mayaGeometry.deformers())

			self.geoMasters[key] = skin
			self.importGeometry(mayaGeometry, groupName, skinType)

 	def buildPrimitive(self, mayaPrimitive, instance):
 		key = "%s_%s" % (mayaPrimitive.mayaJoint.agent.agentType(),
						 mayaPrimitive.mayaJoint.joint().name )
 		try:
 			master = self.primitiveCache[key]
 		except:
 			mayaPrimitive.build()
 			master = mayaPrimitive.name
 			[master] = mc.parent(master, self._getPrimitiveCacheGroup(), relative=True)
 			master = mc.rename( master, key )
 			[master] = mc.ls(master, long=True)
			self.primitiveCache[key] = master
		
		if instance:
			# Use parent -add -noConnections instead of instance for
			# performance reasons. Maya is inherently inefficient in creating
			# instances, often approaching an n^2 runtime for adding a new
			# instance. Some of this overhead is due to reshuffling shading
			# connections every time a new instance is added. Using
			# -noConnections avoids this overhead but leaves the instances
			# unshaded. At the end of the import the shading connections will
			# be applied all at once.
			#
			self._primitivesInstanced = True
			[ shape ] = mc.listRelatives( master, fullPath=True, children=True )
			primitive = mc.duplicate( master, parentOnly=True )
			[primitive] = mc.parent( primitive, world=True )
			mc.parent( shape, primitive, addObject=True, noConnections=True, shape=True )
		else:
			primitive = mc.duplicate( master )
			[primitive] = mc.parent( primitive, world=True )


		mayaPrimitive.name = mc.rename( primitive, mayaPrimitive.baseName )
	
	def buildMaterial(self, material):
		key = ""
		if material.colorMap:
			key = material.colorMap
		else:
			# Currently the only variation that is supported is in the
			# color map. If no color map is specified than it's safe
			# to assume that the id of the material will uniquely map
			# to a Maya shading group. (since the color map can vary
			# a material with a color map may actually map to several
			# Maya shading groups)
			#
			key = str(material.id())
		
		sg = ""
		try:
			sg = self.materialCache[key]
		except:
			# shadingNode doesn't work right in batch mode, so do it manually
			#
			shader = mc.createNode(material.materialType, name=material.name())
			mc.connectAttr( "%s.msg" % shader, ":defaultShaderList1.s", nextAvailable=True)
			
			sg = mc.sets(renderable=True, empty=True, name="%sSG" % shader)
			mc.connectAttr( "%s.outColor" % shader, "%s.surfaceShader" % sg, force=True )	
			MayaUtil.setDouble3Attr( "%s.ambientColor" % shader, material.ambient )
			mc.setAttr( "%s.diffuse" % shader, material.diffuse )
			
			if "blinn" == material.materialType:
				MayaUtil.setDouble3Attr( "%s.specularColor" % shader, material.specular )
				mc.setAttr( "%s.specularRollOff" % shader, material.roughness )			
			
			if material.colorMap:
				file = mc.createNode("file", name="%sFile" % shader)
				mc.connectAttr( "%s.msg" % file, ":defaultTextureList1.tx", nextAvailable=True)
				place = mc.createNode("place2dTexture", name="%sPlace" % shader)
				mc.connectAttr( "%s.msg" % place, ":defaultRenderUtilityList1.u", nextAvailable=True)
		
				mc.connectAttr( "%s.coverage" % place, "%s.coverage" % file, force=True )
				mc.connectAttr( "%s.translateFrame" % place, "%s.translateFrame" % file, force=True )
				mc.connectAttr( "%s.rotateFrame" % place, "%s.rotateFrame" % file, force=True )
				mc.connectAttr( "%s.mirrorU" % place, "%s.mirrorU" % file, force=True )
				mc.connectAttr( "%s.mirrorV" % place, "%s.mirrorV" % file, force=True )
				mc.connectAttr( "%s.stagger" % place, "%s.stagger" % file, force=True )
				mc.connectAttr( "%s.wrapU" % place, "%s.wrapU" % file, force=True )
				mc.connectAttr( "%s.wrapV" % place, "%s.wrapV" % file, force=True )
				mc.connectAttr( "%s.repeatUV" % place, "%s.repeatUV" % file, force=True )
				mc.connectAttr( "%s.offset" % place, "%s.offset" % file, force=True )
				mc.connectAttr( "%s.rotateUV" % place, "%s.rotateUV" % file, force=True )
				mc.connectAttr( "%s.noiseUV" % place, "%s.noiseUV" % file, force=True )
				mc.connectAttr( "%s.vertexUvOne" % place, "%s.vertexUvOne" % file, force=True )
				mc.connectAttr( "%s.vertexUvTwo" % place, "%s.vertexUvTwo" % file, force=True )
				mc.connectAttr( "%s.vertexUvThree" % place, "%s.vertexUvThree" % file, force=True )
				mc.connectAttr( "%s.vertexCameraOne" % place, "%s.vertexCameraOne" % file, force=True )
				mc.connectAttr( "%s.outUV" % place, "%s.uv" % file, force=True )
				mc.connectAttr( "%s.outUvFilterSize" % place, "%s.uvFilterSize" % file, force=True )
			
				mc.connectAttr( "%s.outColor" % file, "%s.color" % shader, force=True )

				mc.setAttr( "%s.fileTextureName" % file, "%s" % material.colorMap, type="string" )
			
			self.materialCache[key] = sg
		
		return sg

	def setClusterWeights( self, geometry, cluster ):
		# TODO: optimize the setAttrs so that the wl.w[x] attrs remain sparse
		mc.setAttr("%s.nw" % cluster, 0)
		clusterKey = geometry.file()
		if clusterKey in self.clusterCache:
			cachedCluster = self.clusterCache[clusterKey]
			mc.connectAttr( "%s.wl" % cachedCluster, "%s.wl" % cluster )
			mc.disconnectAttr( "%s.wl" % cachedCluster, "%s.wl" % cluster )
		else:
			weights = geometry.weights()
			numVerts = len(weights)
			for vtx in range(numVerts):
				attr = "%s.wl[%d].w" % (cluster, vtx)
				MayaUtil.setMultiAttr( attr, weights[vtx] )
			cachedCluster = mc.createNode("skinCluster")
			mc.connectAttr( "%s.wl" % cluster, "%s.wl" % cachedCluster )
			mc.disconnectAttr( "%s.wl" % cluster, "%s.wl" % cachedCluster )
			self.clusterCache[clusterKey] = cachedCluster
		mc.setAttr("%s.nw" % cluster, 1)
		
	def cleanup(self):
		'''Called at the end of an import to cleanup any temporary Maya nodes
		   created by the factory, and perform any postponed operations.'''
		if self._primitivesInstanced:
			# Add the shading assignments that were postponed by the use
			# of the -noConnections flag when creating the instances
			#
			masters = mc.listRelatives(self._getPrimitiveCacheGroup(), allDescendents=True)
			primitives = mc.ls( masters, allPaths=True, type="shape")
			mc.sets( primitives, edit=True, forceElement="initialShadingGroup" )
		else:
			mc.delete(self._getPrimitiveCacheGroup())

		# Clean up any "cache" nodes - nodes which were created only to
		# speed up the creation of other nodes by providing a source
		# to copy from
		#
		mc.delete(self._getGeoMastersGroup())
		for cluster in self.clusterCache.values():
			mc.delete(cluster)

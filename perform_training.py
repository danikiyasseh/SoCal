#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Apr 27 19:44:59 2020

@author: Dani Kiyasseh
"""

#%%

""" Functions in this Script 
    1) meta_single
    2) one_epoch
"""
#%%
import torch
import torch.nn as nn
from operator import itemgetter
from sklearn.metrics import roc_auc_score
import numpy as np
from sklearn.preprocessing import LabelBinarizer
from tqdm import tqdm

from prepare_miscellaneous import obtain_predictions

#%%

def meta_single(phase,inference,classification,criterion,criterion_single,dataloaders,models_list,optimizer,device,weighted_sampling,aul_scaling_dict,hyperparam_dict=None,bptt_steps=None,epoch_count=None,new_task_epochs=None,trial=None,save_path_dir=None,lambda1=1,mask_gradients=False,reg_term=False,current_task_info=None,task_instance_params_dict=None): #b/c it is single, models_list contains one model only
    """ One Forward and Backward Pass for Traditional Finetuning with Single Meta Learner """
    ohe = LabelBinarizer()
    running_loss = 0.0
    running_acc = 0
    outputs_list = []
    labels_list = []
    modality_list = []
    indices_list = []
    task_names_list = []
    loss_list = []
    #hyperparam_dict = dict()
    batch_num = 0
    #criterion_single = nn.CrossEntropyLoss(reduction='none')
    scoring_function = []
    #load data in batches for this phase 
    #torch.manual_seed(0)
    batch = 0
    bptt_counter = 0
    #skip_counter = 0
    #retain_graph = False #(default)
    #print(next(iter(dataloaders[phase])))
    for inputs,labels,modality,task_names,indices in tqdm(dataloaders[phase]):
        """ Print Parameters """
        #print(list(models_list[0].parameters())[0][0])
        batch += 1
        inputs = inputs.to(device)
        labels = labels.to(device)
        """ BCELoss Requires Float Labels BUT CrossEntropy Requires Long Labels """
        if classification is not None and classification != '2-way':
            if labels.dtype != torch.long:
                labels = labels.type(torch.long)
        elif classification == '2-way':
            if labels.dtype != torch.float:
                labels = labels.type(torch.float)
            #print(labels.dtype)
        #print(device)
        #optimizer.zero_grad()
        #print(batch_num)
        with torch.set_grad_enabled('train1' in phase):# and inference == False): #('train' in phase and inference == False)
            if bptt_steps is None:
                outputs = models_list[0](inputs)
                if isinstance(outputs,tuple):
                    outputs, logit_ask = outputs[0], outputs[1]
                
                params = list(models_list[0].named_parameters())
            else:
                outputs, params, hidden_attentions = models_list[0](inputs,bptt_counter)
            
#            """ Continual Learning Branch ----- Obtain Regularization Loss """
#            if epoch_count is not None and new_task_epochs is not None:
#                if reg_term == True:
#                    save_task_params(save_path_dir,epoch_count,new_task_epochs,params,models_list[0])
#                    save_task_masks(save_path_dir,epoch_count,new_task_epochs,params,batch,models_list[0],inputs,labels,criterion,classification,optimizer,mask_type='l2param')
#                    #save_task_masks(save_path_dir,epoch_count,new_task_epochs,params)
#                    #gradient_masks = obtain_snip_mask(batch_num,epoch_count,new_task_epochs,models_list[0],inputs,labels,criterion,classification)
#                    if epoch_count >= new_task_epochs[1]: #no regularization for first task
#                        #similarities = obtain_hyperparam_values(save_path_dir,epoch_count,new_task_epochs,params)
#                        similarities = obtain_hyperparam_values(save_path_dir,epoch_count,new_task_epochs,params,batch,models_list[0],inputs,labels,criterion,classification,optimizer,mask_type='l2param')
#                        reg_loss = obtain_regularization_loss(epoch_count,new_task_epochs,params,save_path_dir,similarities,models_list[0],reg='params')
            
            if classification == '2-way' or classification is None:
                outputs = outputs.squeeze(1)
            #print(outputs.shape,labels.shape)
            if aul_scaling_dict is not None:
                if 'train' in phase:
                    """ AUL Scaling Requires Sample Losses """
                    loss = criterion_single(outputs,labels)
                    indices = list(indices.cpu().detach().numpy())
                    scale = torch.tensor(list(itemgetter(*indices)(aul_scaling_dict)),dtype=torch.float,device=device)
                    #print(scale)
                    loss = torch.matmul(loss.squeeze(),scale.squeeze())/loss.shape[0]
                else:
                    loss = criterion(outputs,labels)
                    #loss_single = criterion_single(outputs,labels)
            else:
                #print(outputs,labels)
                """ De-Mean the Outputs Just in Case Logits are Off """
                outputs = outputs - torch.mean(outputs)
                
                """ Retrieve Subset of Possterior Distribution """
                if trial == 'abstention_penalty':
                    """ Multi-Head Oracle Setup """
                    loss = criterion(outputs,labels)
                    loss_single = criterion_single(outputs,labels)
                    if classification == '2-way':
                        output_probs = torch.sigmoid(outputs)
                        ask_prob = torch.sigmoid(logit_ask)
                        preds = obtain_predictions(output_probs,device,classification)
                        binary_acc = (preds == labels.data.type(torch.long))
                        binary_error = (~binary_acc).type(torch.float).unsqueeze(1)
                    else:
                        output_probs = torch.softmax(outputs,1)
                        ask_prob = torch.sigmoid(logit_ask)
                        preds = obtain_predictions(output_probs,device,classification)
                        binary_acc = (preds == labels.data)
                        binary_error = (~binary_acc).type(torch.float).unsqueeze(1)
                        
                    if hyperparam_dict is not None:
                        if 'train1' in phase:
                            """ Running Average of Zero One Acc Per Instance """
                            for index,zero_one_acc in zip(indices,binary_acc):
                                index = index.item()
                                hyperparam_dict[index] = hyperparam_dict[index] + (1/(epoch_count+1))*(zero_one_acc.type(torch.float) - hyperparam_dict[index])
                    """ Weighting Error Imbalance Loss - (Affects AUC Performance Negatively) """
                    error_count = torch.histc(binary_error,2)
                    ratio = error_count[0].type(torch.float)/error_count[1].type(torch.float)
                    if ratio > 1:
                        ratio = ratio
                    else:
                        ratio = torch.tensor(1)
                    #print('%.4f' % ratio)
                    oracle_criterion = nn.BCEWithLogitsLoss(pos_weight=ratio)
                    #oracle_criterion = nn.BCEWithLogitsLoss(pos_weight=None)
                    oracle_loss = oracle_criterion(logit_ask,binary_error) #ask_prob
                    if hyperparam_dict is not None:
                        oracle_criterion_single = nn.BCEWithLogitsLoss(pos_weight=ratio,reduction='none')
                        oracle_loss_single = oracle_criterion_single(logit_ask,binary_error)
                    
                    indices_right = torch.nonzero(binary_acc)
                    indices_wrong = torch.nonzero(binary_error)
                    reg1 = torch.mean(torch.relu(ask_prob[indices_right] - 0.25))
                    reg2 = torch.mean(torch.relu(ask_prob[indices_wrong] - 0.75))
                    
                    #print(loss,oracle_loss)
                    if hyperparam_dict is not None:
                        if 'train1' in phase: #hyperparams are only for training phase with labelled data
                            """ Add Loss Dependent HyperParam Here - Feb 27, 2020 """
                            hyperparams_batch = torch.tensor([1/hyperparam_dict[index.item()] if i in indices_right else torch.tensor(1,device=device) for i,index in enumerate(indices)],dtype=torch.float,device=device)
                            loss = torch.mean(loss_single + hyperparams_batch*oracle_loss_single)
                            print(hyperparams_batch)
                            #print(len(hyperparams_batch))
                            print(loss)
                        else:
                            loss = loss + oracle_loss
                    else:
                        loss = loss + lambda1*oracle_loss
                    """ Loss That Works """
                    #loss = loss + lambda1*oracle_loss #+ reg1 + reg2
                    #this is just to facilitate procedure outside of this function
                    if len(outputs.shape) == 1:
                        outputs = outputs.unsqueeze(1)
                    outputs = torch.cat((outputs,logit_ask),1)
                    """ End of Multi-Head Oracle Setup """

                    loss_list.append(loss_single.cpu().detach().numpy())
                else:
                    loss = criterion(outputs,labels)
                    loss_single = criterion_single(outputs,labels)
                    loss_list.append(loss_single.cpu().detach().numpy())
                    #print(task_instance_params_dict) #WORKS
                    if task_instance_params_dict is not None:
                        #print('BAZOOKA')
                        task = current_task_info['current_task_dataset']
                        modality = current_task_info['current_modality']
                        leads = current_task_info['current_leads']
                        fraction = current_task_info['current_fraction']
                        class_pair = current_task_info['current_class_pair']
                        name = '-'.join((task,modality[0],str(fraction),leads,class_pair))
                        task_instance_params = task_instance_params_dict[name] #parameter list
                        #print(task_instance_params) #WORKS
                        #print(name)
                        if 'physionet2020' in task: #average across classes in multilabel case
                            loss_single = torch.mean(loss_single,1)
                            #print(loss_single,loss_single.shape)
                        
                        """ IMPLEMENTATION 2 - Loss Coefficient - Faster """
                        if 'train1' in phase:
                            #print(task_names)
                            indices_in_batch_for_current_task = np.where([task_name == name for task_name in task_names])[0]
                            indices_in_batch_for_replayed_items = np.where([task_name != name for task_name in task_names])[0]
                            indices_in_task_instance_params = indices[indices_in_batch_for_current_task]
                            if len(indices_in_batch_for_current_task) !=0 and len(indices_in_batch_for_replayed_items) != 0:   
                                loss_to_add0 = loss_single[indices_in_batch_for_current_task] * task_instance_params[indices_in_task_instance_params]
                                loss_to_add1 = loss_single[indices_in_batch_for_replayed_items]
                                loss = torch.mean(loss_to_add0) + torch.mean(loss_to_add1)
                                loss = loss/2
                            elif len(indices_in_batch_for_current_task) !=0 and len(indices_in_batch_for_replayed_items) == 0:
                                loss_to_add0 = loss_single[indices_in_batch_for_current_task] * task_instance_params[indices_in_task_instance_params]
                                loss = torch.mean(loss_to_add0)
                            elif len(indices_in_batch_for_replayed_items) != 0 and len(indices_in_batch_for_current_task) == 0:
                                loss_to_add1 = loss_single[indices_in_batch_for_replayed_items]
                                loss = torch.mean(loss_to_add1)

                            """ Regularization to Keep Params Close to 1 """
                            #print(task_instance_params)
                            regularization_criterion = nn.MSELoss()
                            task_instance_param_reg_loss = regularization_criterion(task_instance_params[indices_in_task_instance_params],torch.ones_like(task_instance_params[indices_in_task_instance_params]))
                            print(loss,10*task_instance_param_reg_loss)
                            loss = loss + 10*task_instance_param_reg_loss #10 for Class-IL Cardiology
                        else:
                            loss = torch.mean(loss_single)
                        """ End of Implementation 2 """
                    
                    if classification == '2-way':
                        output_probs = torch.sigmoid(outputs)
                    else:
                        output_probs = torch.softmax(outputs,1)
                    preds = obtain_predictions(output_probs,device,classification)
                
#                """ Continual Learning Branch ----- Adding Regularization Loss """
#                if epoch_count is not None and new_task_epochs is not None:
#                    if reg_term == True:
#                        if epoch_count >= new_task_epochs[1]:
#                            #print('Regularizing Loss')
#                            loss = loss + reg_loss
        
        if phase == 'train1': #only perform backprop for train1 phase 
            """ Change retain_graph Based on Continual Learning Specifications """
            if bptt_steps is None:
                retain_graph = False
            else:
                if (bptt_counter+1) % (bptt_steps+1) == 0:
                    retain_graph=False
                else:
                    retain_graph = True
                bptt_counter += 1
            
            #print(retain_graph)            
            loss.backward(retain_graph=retain_graph)
            
#            """ Continual Learning Branch ----- Mask Gradients """
#            if epoch_count is not None and new_task_epochs is not None:
#                if mask_gradients == True:                
#                    save_task_fisher(save_path_dir,epoch_count,new_task_epochs,models_list[0])
#                    if epoch_count >= new_task_epochs[1]:
#                        gradient_masks = obtain_masks_for_gradients(save_path_dir,epoch_count,new_task_epochs,params,models_list[0],matrix='fisher')
#                        mask_gradients_all(dict(models_list[0].named_parameters()),gradient_masks)
            
            """ Network Parameters """
            if isinstance(optimizer,tuple):
                optimizer[0].step()
                """ Task-Instance Parameters """
                optimizer[1].step()
                optimizer[0].zero_grad()
                optimizer[1].zero_grad()
            else:
                optimizer.step()
                optimizer.zero_grad()
            
            #scoring function is only calculated on training set
            if weighted_sampling:
                loss_elements = criterion_single(outputs,labels)
                scoring_function.append(loss_elements)
            
        running_loss += loss.item() * inputs.shape[0]
        if labels.data.dtype != torch.long:
            labels.data = labels.data.type(torch.long)
        #print(preds,labels)
        if classification is not None:
            running_acc += torch.sum(preds==labels.data)

        outputs_list.append(outputs.cpu().detach().numpy())
        labels_list.append(labels.cpu().detach().numpy())
        modality_list.append(modality)
        indices_list.append(indices)
        task_names_list.append(task_names)
        batch_num += 1
    
    if weighted_sampling and phase == 'train':
        scoring_function = torch.cat(scoring_function) #flatten the list
        
    epoch_loss = running_loss / len(dataloaders[phase].dataset)
    if classification is not None:
        epoch_acc = running_acc.double() / len(dataloaders[phase].dataset)
    else:
        epoch_acc = float('inf')
    #epoch_auroc = roc_auc_score(ohe.fit_transform(np.concatenate(labels_list)),np.concatenate(outputs_list),average='macro') #np.concatenate here helps flatten list                

    """ Added New """
    #print(labels_list)
    labels = np.concatenate(labels_list)
    labels_ohe = ohe.fit_transform(labels)
    probs = np.concatenate(outputs_list)
    if trial == 'abstention_penalty':
        probs = probs[:,:-1] #last column is abstention probs
    indices = np.concatenate(indices_list)
    task_names = np.concatenate(task_names_list)
    
    if 'train1' in phase or 'val' in phase or 'test' in phase:
        if classification is not None and classification != '2-way':
            """ MultiClass AUROC """
            all_auc = []
            for i in range(labels_ohe.shape[1]):
                auc = roc_auc_score(labels_ohe[:,i],probs[:,i])
                all_auc.append(auc)
            epoch_auroc = np.mean(all_auc)
        elif classification == '2-way':
            """ Binary AUROC """
            epoch_auroc = roc_auc_score(labels,probs)
            
            if 'physionet2020' in save_path_dir:
                """ Use This for MultiLabel Process -- Only for Physionet2020 """
                all_auc = []
                for i in range(labels_ohe.shape[1]):
                #print(labels[:,i],probs[:,i])
                    auc = roc_auc_score(labels_ohe[:,i],probs[:,i])
                    #print(auc)
                    all_auc.append(auc)
                epoch_auroc = np.mean(all_auc)
                
    else:
        epoch_auroc = float('inf')

    return epoch_loss, epoch_acc, epoch_auroc, outputs_list, labels_list, modality_list, indices_list, task_names_list, loss_list, hyperparam_dict

def one_epoch(mixture,classification,criterion,criterion_single,weighted_sampling,phase,inference,dataloaders_list,models_list,mix_coefs,optimizer,device,bptt_steps=None,aul_scaling_dict=None,hyperparam_dict=None,epoch_count=None,new_task_epochs=None,trial=None,save_path_dir=None,lambda1=1,mask_gradients=False,reg_term=False,current_task_info=None,task_instance_params_dict=None):
    """ One epochs' worth of training and validation """
    #if mixture is not False:
    dataloaders = dataloaders_list[0] #only one dataset - dataloaders should be a dict
    #print(next(iter(dataloaders['train'])))
    epoch_loss, epoch_acc, epoch_auroc, outputs_list, labels_list, modality_list, indices_list, task_names_list, scoring_function, hyperparam_dict = meta_single(phase,inference,classification,criterion,criterion_single,dataloaders,models_list,optimizer,device,weighted_sampling,aul_scaling_dict,hyperparam_dict=hyperparam_dict,bptt_steps=bptt_steps,epoch_count=epoch_count,new_task_epochs=new_task_epochs,trial=trial,save_path_dir=save_path_dir,lambda1=lambda1,mask_gradients=mask_gradients,reg_term=reg_term,current_task_info=current_task_info,task_instance_params_dict=task_instance_params_dict)
    mix_coefs = None
    return {"epoch_loss": epoch_loss, "epoch_acc": epoch_acc, "epoch_auroc": epoch_auroc}, outputs_list, labels_list, mix_coefs, modality_list, indices_list, task_names_list, scoring_function, hyperparam_dict
